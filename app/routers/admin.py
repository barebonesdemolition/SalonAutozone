import os
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc
from typing import Optional
from jose import jwt, JWTError

from app.db import get_db
from app import models
from app.config import get_settings

router = APIRouter(prefix="/api/admin", tags=["Admin"])
settings = get_settings()

# Simple shared secret for admin access — set this in Render env vars
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "salon-admin-2026")


async def require_admin(
    x_admin_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Require admin access. Either via X-Admin-Key header OR logged-in admin user."""
    # Path 1: shared secret (simple, for your personal use)
    if x_admin_key and x_admin_key == ADMIN_SECRET:
        return {"via": "secret"}

    # Path 2: JWT of an admin user
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
            user_id = int(payload.get("sub"))
            result = await db.execute(select(models.User).where(models.User.id == user_id))
            user = result.scalars().first()
            if user and user.is_admin:
                return {"via": "user", "user": user}
        except (JWTError, TypeError, ValueError):
            pass

    raise HTTPException(status_code=403, detail="Admin access required")


# ============================================================
# DASHBOARD STATS
# ============================================================
@router.get("/stats")
async def admin_stats(
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    total_users = (await db.execute(select(func.count(models.User.id)))).scalar() or 0
    total_vehicles = (await db.execute(select(func.count(models.VehicleListing.id)))).scalar() or 0
    total_parts = (await db.execute(select(func.count(models.PartListing.id)))).scalar() or 0
    total_inquiries = (await db.execute(select(func.count(models.Inquiry.id)))).scalar() or 0
    total_imports = (await db.execute(select(func.count(models.ImportRequest.id)))).scalar() or 0
    total_saved = (await db.execute(select(func.count(models.SavedListing.id)))).scalar() or 0
    total_garages = (await db.execute(select(func.count(models.Garage.id)))).scalar() or 0

    featured_parts = (await db.execute(
        select(func.count(models.PartListing.id)).where(models.PartListing.is_featured == True)
    )).scalar() or 0
    featured_vehicles = (await db.execute(
        select(func.count(models.VehicleListing.id)).where(models.VehicleListing.is_featured == True)
    )).scalar() or 0

    # Revenue estimate: featured listings × 150,000 SLL
    featured_revenue = (featured_parts + featured_vehicles) * 150000

    return {
        "total_users": total_users,
        "total_vehicles": total_vehicles,
        "total_parts": total_parts,
        "total_inquiries": total_inquiries,
        "total_imports": total_imports,
        "total_saved": total_saved,
        "total_garages": total_garages,
        "featured_parts": featured_parts,
        "featured_vehicles": featured_vehicles,
        "featured_revenue_sll": featured_revenue,
    }


# ============================================================
# FEATURED LISTINGS (revenue tracker)
# ============================================================
@router.get("/featured")
async def admin_featured(
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Every currently featured part and vehicle with owner info."""
    parts_result = await db.execute(
        select(models.PartListing)
        .where(models.PartListing.is_featured == True)
        .order_by(desc(models.PartListing.featured_until))
    )
    parts = parts_result.scalars().all()

    vehicles_result = await db.execute(
        select(models.VehicleListing)
        .where(models.VehicleListing.is_featured == True)
        .order_by(desc(models.VehicleListing.featured_until))
    )
    vehicles = vehicles_result.scalars().all()

    # Get owners in one query
    user_ids = set()
    for p in parts:
        if p.vendor_id: user_ids.add(p.vendor_id)
    for v in vehicles:
        if v.seller_id: user_ids.add(v.seller_id)

    owners = {}
    if user_ids:
        users_result = await db.execute(
            select(models.User).where(models.User.id.in_(user_ids))
        )
        for u in users_result.scalars().all():
            owners[u.id] = {"name": u.full_name, "email": u.email, "phone": u.phone}

    def fmt_part(p):
        return {
            "id": p.id,
            "type": "part",
            "title": p.name,
            "category": p.category,
            "price_sll": p.price_sll,
            "location": p.location,
            "featured_until": p.featured_until.isoformat() if p.featured_until else None,
            "owner": owners.get(p.vendor_id, {}),
        }

    def fmt_vehicle(v):
        return {
            "id": v.id,
            "type": "vehicle",
            "title": v.title,
            "category": "vehicle",
            "price_sll": v.price_sll,
            "location": v.location,
            "featured_until": v.featured_until.isoformat() if v.featured_until else None,
            "owner": owners.get(v.seller_id, {}),
        }

    items = [fmt_part(p) for p in parts] + [fmt_vehicle(v) for v in vehicles]
    return {
        "count": len(items),
        "revenue_sll": len(items) * 150000,
        "items": items,
    }


# ============================================================
# UNFEATURE (admin can remove featured status)
# ============================================================
@router.post("/unfeature/{listing_type}/{listing_id}")
async def admin_unfeature(
    listing_type: str,
    listing_id: int,
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if listing_type == "part":
        result = await db.execute(select(models.PartListing).where(models.PartListing.id == listing_id))
        item = result.scalars().first()
    elif listing_type == "vehicle":
        result = await db.execute(select(models.VehicleListing).where(models.VehicleListing.id == listing_id))
        item = result.scalars().first()
    else:
        raise HTTPException(status_code=400, detail="listing_type must be 'part' or 'vehicle'")

    if not item:
        raise HTTPException(status_code=404, detail="Listing not found")

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
    result = await db.execute(
        select(models.User).order_by(desc(models.User.id)).limit(limit)
    )
    users = result.scalars().all()
    return {
        "count": len(users),
        "users": [{
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "phone": u.phone,
            "is_vendor": u.is_vendor,
            "is_admin": u.is_admin,
            "is_active": u.is_active,
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
    result = await db.execute(
        select(models.ImportRequest).order_by(desc(models.ImportRequest.created_at)).limit(limit)
    )
    items = result.scalars().all()
    return {
        "count": len(items),
        "imports": [{
            "id": r.id,
            "part_name": r.part_name,
            "car_make": r.car_make,
            "car_model": r.car_model,
            "car_year": r.car_year,
            "customer_name": r.customer_name,
            "customer_phone": r.customer_phone,
            "status": r.status,
            "budget_sll": r.budget_sll,
            "urgency": r.urgency,
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
    result = await db.execute(
        select(models.Inquiry).order_by(desc(models.Inquiry.created_at)).limit(limit)
    )
    items = result.scalars().all()
    return {
        "count": len(items),
        "inquiries": [{
            "id": i.id,
            "listing_type": i.listing_type,
            "listing_id": i.listing_id,
            "listing_title": i.listing_title,
            "buyer_name": i.buyer_name,
            "buyer_phone": i.buyer_phone,
            "buyer_message": i.buyer_message,
            "status": i.status,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        } for i in items],
    }
