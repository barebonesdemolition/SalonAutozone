import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app import models
from app.auth import get_current_user
from app.db import get_db

router = APIRouter(prefix="/api/my-account", tags=["My Account"])

V = models.VehicleListing
P = models.PartListing
I = models.Inquiry


# ============================================================
# Schemas
# ============================================================

class SavedListingCreate(BaseModel):
    listing_type: str  # "part" or "vehicle"
    listing_id: int
    notes: Optional[str] = None

    @field_validator("listing_type")
    @classmethod
    def validate_listing_type(cls, v: str) -> str:
        if v not in ("part", "vehicle"):
            raise ValueError("listing_type must be 'part' or 'vehicle'")
        return v


# ============================================================
# Helpers
# ============================================================

def _digits(phone) -> str:
    return re.sub(r"\D", "", phone or "")


def _phone_is(column, phone):
    """Match a phone column ignoring spaces, +, dashes. Returns None if the user has no phone,
    so callers return nothing instead of matching every row with an empty phone."""
    d = _digits(phone)
    if len(d) < 7:
        return None
    # Compare the last 8 digits so +232 76 123456, 076123456 and 76123456 all match
    return func.right(func.regexp_replace(column, r"[^0-9]", "", "g"), 8) == d[-8:]


def _active_featured(obj, now) -> bool:
    return bool(obj.is_featured) and (obj.featured_until is None or obj.featured_until > now)


def _featured_sql(model, now):
    return and_(model.is_featured.is_(True), or_(model.featured_until.is_(None), model.featured_until > now))


def _inquiry_counts(listing_type, ids):
    return (
        select(I.listing_id, func.count(I.id))
        .where(I.listing_type == listing_type, I.listing_id.in_(ids))
        .group_by(I.listing_id)
    )


def _iso(dt):
    return dt.isoformat() if dt else None


def _received_condition(user_id):
    vids = select(V.id).where(V.seller_id == user_id)
    pids = select(P.id).where(P.vendor_id == user_id)
    return or_(
        and_(I.listing_type == "vehicle", I.listing_id.in_(vids)),
        and_(I.listing_type == "part", I.listing_id.in_(pids)),
    )


# ============================================================
# MY CARS
# ============================================================

@router.get("/my-cars")
async def my_cars(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    vehicles = (
        await db.execute(
            select(V).where(V.seller_id == user.id).order_by(V.is_featured.desc(), desc(V.created_at))
        )
    ).scalars().all()

    inquiry_map = {}
    if vehicles:
        rows = await db.execute(_inquiry_counts("vehicle", [v.id for v in vehicles]))
        inquiry_map = {r[0]: r[1] for r in rows.all()}

    now = datetime.utcnow()
    output = [
        {
            "id": v.id, "title": v.title, "make": v.make, "model": v.model, "year": v.year,
            "price_sll": v.price_sll, "price_usd": v.price_usd, "location": v.location,
            "image_url": v.image_url, "is_sold": v.is_sold,
            "inquiry_count": inquiry_map.get(v.id, 0),
            "is_featured": _active_featured(v, now),
            "featured_until": _iso(v.featured_until),
            "views": v.views or 0, "created_at": _iso(v.created_at),
        }
        for v in vehicles
    ]
    return {"count": len(output), "vehicles": output}


# ============================================================
# MY PARTS
# ============================================================

@router.get("/my-parts")
async def my_parts(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    parts = (
        await db.execute(
            select(P).where(P.vendor_id == user.id).order_by(P.is_featured.desc(), desc(P.created_at))
        )
    ).scalars().all()

    inquiry_map = {}
    if parts:
        rows = await db.execute(_inquiry_counts("part", [p.id for p in parts]))
        inquiry_map = {r[0]: r[1] for r in rows.all()}

    now = datetime.utcnow()
    output = [
        {
            "id": p.id, "name": p.name, "category": p.category,
            "compatible_make": p.compatible_make, "compatible_model": p.compatible_model,
            "price_sll": p.price_sll, "stock_quantity": p.stock_quantity, "condition": p.condition,
            "location": p.location, "image_url": p.image_url,
            "inquiry_count": inquiry_map.get(p.id, 0),
            "is_featured": _active_featured(p, now),
            "featured_until": _iso(p.featured_until),
            "views": p.views or 0, "created_at": _iso(p.created_at),
        }
        for p in parts
    ]
    return {"count": len(output), "parts": output}


# ============================================================
# RECEIVED INQUIRIES (people asking about MY listings)
# ============================================================

@router.get("/received-inquiries")
async def received_inquiries(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    inquiries = (
        await db.execute(select(I).where(_received_condition(user.id)).order_by(desc(I.created_at)))
    ).scalars().all()
    output = [
        {
            "id": i.id, "listing_type": i.listing_type, "listing_id": i.listing_id,
            "listing_title": i.listing_title, "buyer_name": i.buyer_name, "buyer_phone": i.buyer_phone,
            "buyer_message": i.buyer_message, "status": i.status, "created_at": _iso(i.created_at),
        }
        for i in inquiries
    ]
    return {"count": len(output), "inquiries": output}


# ============================================================
# MY INQUIRIES (SENT)
# ============================================================

@router.get("/my-inquiries")
async def my_inquiries(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    cond = _phone_is(I.buyer_phone, user.phone)
    if cond is None:
        return {"count": 0, "inquiries": []}
    inquiries = (await db.execute(select(I).where(cond).order_by(desc(I.created_at)))).scalars().all()
    output = [
        {
            "id": i.id, "listing_type": i.listing_type, "listing_id": i.listing_id,
            "listing_title": i.listing_title, "buyer_message": i.buyer_message, "status": i.status,
            "seller_phone": i.seller_phone, "created_at": _iso(i.created_at),
        }
        for i in inquiries
    ]
    return {"count": len(output), "inquiries": output}


# ============================================================
# MY IMPORT REQUESTS
# ============================================================

@router.get("/my-import-requests")
async def my_import_requests(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    R = models.ImportRequest
    cond = _phone_is(R.customer_phone, user.phone)
    if cond is None:
        return {"count": 0, "requests": []}
    requests = (await db.execute(select(R).where(cond).order_by(desc(R.created_at)))).scalars().all()
    output = [
        {
            "id": r.id, "part_name": r.part_name, "car_make": r.car_make, "car_model": r.car_model,
            "car_year": r.car_year, "quantity": r.quantity, "budget_sll": r.budget_sll,
            "urgency": r.urgency, "status": r.status, "quoted_price_sll": r.quoted_price_sll,
            "estimated_days": r.estimated_days, "supplier_country": r.supplier_country,
            "admin_notes": r.admin_notes, "created_at": _iso(r.created_at),
        }
        for r in requests
    ]
    return {"count": len(output), "requests": output}


# ============================================================
# STATS (a handful of queries instead of ten)
# ============================================================

@router.get("/stats")
async def my_stats(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    now = datetime.utcnow()

    cars = (
        await db.execute(
            select(
                func.count(V.id),
                func.count(V.id).filter(V.is_sold.is_(True)),
                func.count(V.id).filter(_featured_sql(V, now)),
            ).where(V.seller_id == user.id)
        )
    ).one()
    parts = (
        await db.execute(
            select(func.count(P.id), func.count(P.id).filter(_featured_sql(P, now))).where(P.vendor_id == user.id)
        )
    ).one()

    inquiries_sent = 0
    import_requests = 0
    cond = _phone_is(I.buyer_phone, user.phone)
    if cond is not None:
        inquiries_sent = (await db.execute(select(func.count(I.id)).where(cond))).scalar() or 0
    rcond = _phone_is(models.ImportRequest.customer_phone, user.phone)
    if rcond is not None:
        import_requests = (await db.execute(select(func.count(models.ImportRequest.id)).where(rcond))).scalar() or 0

    received = (await db.execute(select(func.count(I.id)).where(_received_condition(user.id)))).scalar() or 0

    saved_count = 0
    try:
        saved_count = (
            await db.execute(select(func.count(models.SavedListing.id)).where(models.SavedListing.user_id == user.id))
        ).scalar() or 0
    except Exception:
        pass

    return {
        "total_cars": cars[0], "cars_sold": cars[1], "featured_cars": cars[2],
        "total_parts": parts[0], "featured_parts": parts[1],
        "total_inquiries_sent": inquiries_sent, "total_inquiries_received": received,
        "total_import_requests": import_requests, "saved_items": saved_count,
    }


# ============================================================
# SAVED LISTINGS
# ============================================================

@router.get("/saved")
async def list_saved(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        items = (
            await db.execute(
                select(models.SavedListing)
                .where(models.SavedListing.user_id == user.id)
                .order_by(desc(models.SavedListing.created_at))
            )
        ).scalars().all()
    except Exception:
        return {"count": 0, "items": [], "warning": "saved_listings table not created yet"}

    output = [
        {
            "id": s.id, "listing_type": s.listing_type, "listing_id": s.listing_id,
            "listing_title": s.listing_title, "listing_price_sll": s.listing_price_sll,
            "listing_image_url": s.listing_image_url, "notes": s.notes, "created_at": _iso(s.created_at),
        }
        for s in items
    ]
    return {"count": len(output), "items": output}


@router.post("/saved", status_code=status.HTTP_201_CREATED)
async def save_listing(
    item: SavedListingCreate,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    S = models.SavedListing
    existing = await db.execute(
        select(S).where(S.user_id == user.id, S.listing_type == item.listing_type, S.listing_id == item.listing_id)
    )
    if existing.scalars().first():
        return {"success": True, "already_saved": True}

    if item.listing_type == "part":
        p = (await db.execute(select(P).where(P.id == item.listing_id))).scalars().first()
        if not p:
            raise HTTPException(status_code=404, detail="Part not found")
        title, price, image = p.name, p.price_sll, p.image_url
    else:
        v = (await db.execute(select(V).where(V.id == item.listing_id))).scalars().first()
        if not v:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        title, price, image = v.title, v.price_sll, v.image_url

    saved = S(
        user_id=user.id, listing_type=item.listing_type, listing_id=item.listing_id,
        listing_title=title, listing_price_sll=price, listing_image_url=image, notes=item.notes,
    )
    db.add(saved)
    await db.commit()
    await db.refresh(saved)
    return {"success": True, "id": saved.id}


@router.delete("/saved/{saved_id}")
async def unsave_listing(
    saved_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    S = models.SavedListing
    item = (await db.execute(select(S).where(S.id == saved_id, S.user_id == user.id))).scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Saved item not found")
    await db.delete(item)
    await db.commit()
    return {"success": True}


# ============================================================
# DELETE MY LISTINGS
# ============================================================

@router.delete("/vehicle/{vehicle_id}")
async def delete_my_vehicle(
    vehicle_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    vehicle = (await db.execute(select(V).where(V.id == vehicle_id))).scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if vehicle.seller_id != user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own listings")
    await db.delete(vehicle)
    await db.commit()
    return {"message": "Deleted", "id": vehicle_id}


@router.delete("/part/{part_id}")
async def delete_my_part(
    part_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    part = (await db.execute(select(P).where(P.id == part_id))).scalars().first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if part.vendor_id != user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own listings")
    await db.delete(part)
    await db.commit()
    return {"message": "Deleted", "id": part_id}
