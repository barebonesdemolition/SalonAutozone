import hmac
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from jose import JWTError, jwt
from sqlalchemy import and_, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app import models
from app.config import get_settings
from app.db import get_db

router = APIRouter(prefix="/api/admin", tags=["Admin"])
settings = get_settings()

# Shared secret for admin access. Set ADMIN_SECRET in your hosting environment (a long random value).
# There is deliberately NO default: if it is not set, the secret-key route is switched off.
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")
FEATURE_PRICE_SLL = 150000


def _secret_ok(provided: Optional[str]) -> bool:
    if not ADMIN_SECRET or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), ADMIN_SECRET.encode("utf-8"))


async def require_admin(
    x_admin_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Admin access via X-Admin-Key header OR the login token of an active admin user."""
    if _secret_ok(x_admin_key):
        return {"via": "secret"}

    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
            user_id = int(payload.get("sub"))
            result = await db.execute(select(models.User).where(models.User.id == user_id))
            user = result.scalars().first()
            if user and user.is_admin and user.is_active is not False:
                return {"via": "user", "user": user}
        except (JWTError, TypeError, ValueError):
            pass

    raise HTTPException(status_code=403, detail="Admin access required")


def _active_featured(model, now):
    return and_(model.is_featured.is_(True), or_(model.featured_until.is_(None), model.featured_until > now))


async def _get_listing(db: AsyncSession, listing_type: str, listing_id: int):
    if listing_type == "part":
        model = models.PartListing
    elif listing_type == "vehicle":
        model = models.VehicleListing
    else:
        raise HTTPException(status_code=400, detail="listing_type must be 'part' or 'vehicle'")
    item = (await db.execute(select(model).where(model.id == listing_id))).scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Listing not found")
    return item


# ============================================================
# DASHBOARD STATS
# ============================================================
@router.get("/stats")
async def admin_stats(_: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    async def count(col):
        return (await db.execute(select(func.count(col)))).scalar() or 0

    now = datetime.utcnow()
    featured_parts = (await db.execute(
        select(func.count(models.PartListing.id)).where(_active_featured(models.PartListing, now))
    )).scalar() or 0
    featured_vehicles = (await db.execute(
        select(func.count(models.VehicleListing.id)).where(_active_featured(models.VehicleListing, now))
    )).scalar() or 0

    return {
        "total_users": await count(models.User.id),
        "total_vehicles": await count(models.VehicleListing.id),
        "total_parts": await count(models.PartListing.id),
        "total_inquiries": await count(models.Inquiry.id),
        "total_imports": await count(models.ImportRequest.id),
        "total_saved": await count(models.SavedListing.id),
        "total_garages": await count(models.Garage.id),
        "featured_parts": featured_parts,
        "featured_vehicles": featured_vehicles,
        "featured_revenue_sll": (featured_parts + featured_vehicles) * FEATURE_PRICE_SLL,
    }


# ============================================================
# FEATURED LISTINGS (revenue tracker, active ones only)
# ============================================================
@router.get("/featured")
async def admin_featured(_: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    now = datetime.utcnow()
    parts = (await db.execute(
        select(models.PartListing).where(_active_featured(models.PartListing, now))
        .order_by(desc(models.PartListing.featured_until))
    )).scalars().all()
    vehicles = (await db.execute(
        select(models.VehicleListing).where(_active_featured(models.VehicleListing, now))
        .order_by(desc(models.VehicleListing.featured_until))
    )).scalars().all()

    user_ids = {p.vendor_id for p in parts if p.vendor_id} | {v.seller_id for v in vehicles if v.seller_id}
    owners = {}
    if user_ids:
        users = (await db.execute(select(models.User).where(models.User.id.in_(user_ids)))).scalars().all()
        owners = {u.id: {"name": u.full_name, "email": u.email, "phone": u.phone} for u in users}

    def row(kind, item, title, category, owner_id):
        return {
            "id": item.id, "type": kind, "title": title, "category": category,
            "price_sll": item.price_sll, "location": item.location,
            "featured_until": item.featured_until.isoformat() if item.featured_until else None,
            "owner": owners.get(owner_id, {}),
        }

    items = [row("part", p, p.name, p.category, p.vendor_id) for p in parts] + \
            [row("vehicle", v, v.title, "vehicle", v.seller_id) for v in vehicles]
    return {"count": len(items), "revenue_sll": len(items) * FEATURE_PRICE_SLL, "items": items}


# ============================================================
# PROMOTE / UNFEATURE (use this after a seller's Orange Money payment arrives)
# ============================================================
@router.post("/promote/{listing_type}/{listing_id}")
async def admin_promote(
    listing_type: str,
    listing_id: int,
    days: int = Query(7, ge=1, le=90),
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_listing(db, listing_type, listing_id)
    now = datetime.utcnow()
    base = item.featured_until if (item.featured_until and item.featured_until > now) else now
    item.featured_until = base + timedelta(days=days)
    item.is_featured = True
    await db.commit()
    return {"success": True, "featured_until": item.featured_until.isoformat()}


@router.post("/unfeature/{listing_type}/{listing_id}")
async def admin_unfeature(
    listing_type: str,
    listing_id: int,
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    item = await _get_listing(db, listing_type, listing_id)
    item.is_featured = False
    item.featured_until = None
    await db.commit()
    return {"success": True}


# ============================================================
# USERS
# ============================================================
@router.get("/users")
async def admin_users(
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
):
    users = (await db.execute(select(models.User).order_by(desc(models.User.id)).limit(limit))).scalars().all()
    return {
        "count": len(users),
        "users": [{
            "id": u.id, "full_name": u.full_name, "email": u.email, "phone": u.phone,
            "is_vendor": u.is_vendor, "is_admin": u.is_admin, "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        } for u in users],
    }


# ============================================================
# IMPORTS (needs admin action)
# ============================================================
@router.get("/imports")
async def admin_imports(
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
):
    items = (await db.execute(
        select(models.ImportRequest).order_by(desc(models.ImportRequest.created_at)).limit(limit)
    )).scalars().all()
    return {
        "count": len(items),
        "imports": [{
            "id": r.id, "part_name": r.part_name, "car_make": r.car_make, "car_model": r.car_model,
            "car_year": r.car_year, "customer_name": r.customer_name, "customer_phone": r.customer_phone,
            "status": r.status, "budget_sll": r.budget_sll, "urgency": r.urgency,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in items],
    }


# ============================================================
# INQUIRIES
# ============================================================
@router.get("/inquiries")
async def admin_inquiries(
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
):
    items = (await db.execute(
        select(models.Inquiry).order_by(desc(models.Inquiry.created_at)).limit(limit)
    )).scalars().all()
    return {
        "count": len(items),
        "inquiries": [{
            "id": i.id, "listing_type": i.listing_type, "listing_id": i.listing_id,
            "listing_title": i.listing_title, "buyer_name": i.buyer_name, "buyer_phone": i.buyer_phone,
            "buyer_message": i.buyer_message, "status": i.status,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        } for i in items],
    }
