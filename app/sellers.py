"""
Seller trust: public seller profiles, ratings after delivery, verified badges.

- A buyer can rate the seller once per delivered order (1-5 stars and a comment).
- /api/sellers/{id} is the seller's public profile: name, business and whether it
  is verified, rating, number of delivered sales, and recent reviews.
- /api/sellers/badges?ids=… gives verified/rating for many sellers at once, for
  listing cards.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, String, Table, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_user
from app.businesses import BUSINESS_TYPES
from app.db import engine, get_db
from app.shop import orders as orders_t

_md = MetaData()
Table("users", _md, Column("id", Integer, primary_key=True))
reviews = Table(
    "seller_reviews", _md,
    Column("id", Integer, primary_key=True),
    Column("order_id", Integer, nullable=False, unique=True),
    Column("seller_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("buyer_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("rating", Integer, nullable=False),
    Column("comment", String(500), nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
)

router = APIRouter(tags=["Sellers"])
USERS = models.User.__table__
BIZ = models.Business.__table__


async def ensure_review_tables():
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: _md.create_all(c, tables=[reviews], checkfirst=True))


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)


def _first_name(full_name: str | None) -> str:
    return (full_name or "A buyer").strip().split(" ")[0]


@router.post("/api/shop/orders/{order_id}/review", status_code=201)
async def review_order(order_id: int, body: ReviewIn, user: models.User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    order = (await db.execute(select(orders_t).where(orders_t.c.id == order_id, orders_t.c.buyer_id == user.id))).first()
    if not order:
        raise HTTPException(404, "Order not found")
    if order.status != "delivered":
        raise HTTPException(409, "You can rate the seller once your parts are delivered")
    try:
        await db.execute(insert(reviews).values(
            order_id=order_id, seller_id=order.seller_id, buyer_id=user.id, rating=body.rating,
            comment=(body.comment or "").strip() or None, created_at=datetime.utcnow()))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "You've already rated this order")
    return {"order_id": order_id, "rating": body.rating}


@router.get("/api/shop/orders/{order_id}/review")
async def my_review(order_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(reviews).where(reviews.c.order_id == order_id, reviews.c.buyer_id == user.id))).first()
    return {"review": {"rating": row.rating, "comment": row.comment} if row else None}


async def _ratings(db: AsyncSession, seller_ids: list[int]) -> dict[int, tuple[float, int]]:
    rows = (await db.execute(
        select(reviews.c.seller_id, func.avg(reviews.c.rating), func.count(reviews.c.id))
        .where(reviews.c.seller_id.in_(seller_ids)).group_by(reviews.c.seller_id))).all()
    return {r[0]: (round(float(r[1]), 1), int(r[2])) for r in rows}


async def _verified(db: AsyncSession, seller_ids: list[int]) -> set[int]:
    rows = (await db.execute(select(BIZ.c.owner_id).where(BIZ.c.owner_id.in_(seller_ids), BIZ.c.status == "verified"))).all()
    return {r[0] for r in rows}


@router.get("/api/sellers/badges")
async def badges(ids: str = Query(..., max_length=2000), db: AsyncSession = Depends(get_db)):
    seller_ids = sorted({int(x) for x in ids.split(",") if x.strip().isdigit()})[:100]
    if not seller_ids:
        return {"sellers": {}}
    rating, verified = await _ratings(db, seller_ids), await _verified(db, seller_ids)
    return {"sellers": {str(i): {"verified": i in verified, "rating": rating.get(i, (None, 0))[0],
                                 "reviews": rating.get(i, (None, 0))[1]} for i in seller_ids}}


@router.get("/api/sellers/{seller_id}")
async def seller_profile(seller_id: int, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(USERS.c.id, USERS.c.full_name, USERS.c.created_at).where(USERS.c.id == seller_id))).first()
    if not user:
        raise HTTPException(404, "Seller not found")
    biz = (await db.execute(select(BIZ).where(BIZ.c.owner_id == seller_id))).first()
    avg, count = (await _ratings(db, [seller_id])).get(seller_id, (None, 0))
    sold = (await db.execute(select(func.count(orders_t.c.id)).where(
        orders_t.c.seller_id == seller_id, orders_t.c.status == "delivered"))).scalar() or 0
    parts = (await db.execute(select(func.count(models.PartListing.id)).where(
        models.PartListing.vendor_id == seller_id, models.PartListing.stock_quantity > 0))).scalar() or 0
    cars = (await db.execute(select(func.count(models.VehicleListing.id)).where(
        models.VehicleListing.seller_id == seller_id, models.VehicleListing.is_sold.is_(False)))).scalar() or 0
    recent = (await db.execute(
        select(reviews.c.rating, reviews.c.comment, reviews.c.created_at, USERS.c.full_name)
        .select_from(reviews.outerjoin(USERS, USERS.c.id == reviews.c.buyer_id))
        .where(reviews.c.seller_id == seller_id).order_by(reviews.c.id.desc()).limit(10))).all()
    business = None
    if biz and biz.status != "rejected":
        business = {"name": biz.name, "slug": biz.slug if biz.status == "verified" else None,
                    "verified": biz.status == "verified", "type_label": BUSINESS_TYPES.get(biz.business_type, biz.business_type),
                    "city": biz.city}
    return {
        "id": user.id,
        "name": business["name"] if business and business["verified"] else _first_name(user.full_name),
        "member_since": user.created_at.isoformat() if user.created_at else None,
        "business": business,
        "rating": avg, "reviews_count": count, "delivered_sales": sold,
        "listings": {"parts": parts, "cars": cars},
        "reviews": [{"rating": r.rating, "comment": r.comment, "by": _first_name(r.full_name),
                     "date": r.created_at.isoformat() if r.created_at else None} for r in recent],
    }
