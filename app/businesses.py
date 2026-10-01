"""
Business accounts: local parts stores, car dealerships, shippers/importers and wholesalers abroad.
"""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import desc, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app import models
from app.auth import get_current_user
from app.db import get_db
from app.routers.admin import require_admin

router = APIRouter(prefix="/api/businesses", tags=["Businesses"])

BUSINESS_TYPES = {
    "store": "Local parts store",
    "dealership": "Car dealership",
    "shipper": "Shipper / importer",
    "wholesaler": "Wholesaler (abroad)",
}
STATUSES = {"pending", "verified", "rejected", "suspended"}

Business = models.Business


# ---------- helpers ----------

def _wa_digits(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 9 and d.startswith("0"):
        return "232" + d[1:]
    if len(d) == 8:
        return "232" + d
    return d


async def _unique_slug(db: AsyncSession, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "business"
    slug, n = base, 1
    while (await db.execute(select(Business.id).where(Business.slug == slug))).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


def _public(b: Business) -> dict:
    return {
        "id": b.id,
        "slug": b.slug,
        "business_type": b.business_type,
        "type_label": BUSINESS_TYPES.get(b.business_type, b.business_type),
        "name": b.name,
        "country": b.country,
        "city": b.city,
        "whatsapp": b.whatsapp,
        "description": b.description,
        "logo_url": b.logo_url,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


# ---------- schemas ----------

class BusinessApply(BaseModel):
    business_type: str
    name: str
    country: str = "Sierra Leone"
    city: str
    whatsapp: str
    email: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None

    @field_validator("business_type")
    @classmethod
    def _type(cls, v):
        if v not in BUSINESS_TYPES:
            raise ValueError("Choose a valid business type")
        return v

    @field_validator("name", "city", "country")
    @classmethod
    def _text(cls, v):
        v = (v or "").strip()
        if not 2 <= len(v) <= 80:
            raise ValueError("Must be 2 to 80 characters")
        return v

    @field_validator("whatsapp")
    @classmethod
    def _wa(cls, v):
        d = _wa_digits(v)
        if len(d) < 8 or len(d) > 15:
            raise ValueError("Enter a valid WhatsApp number")
        return d

    @field_validator("description")
    @classmethod
    def _desc(cls, v):
        return v.strip()[:1000] if v else None

    @field_validator("logo_url")
    @classmethod
    def _logo(cls, v):
        v = (v or "").strip()
        if not v:
            return None
        if not (v.startswith("https://") or (v.startswith("/") and not v.startswith("//"))):
            raise ValueError("Logo must be an https link or relative path")
        return v


# ---------- owner routes ----------

@router.post("/apply", status_code=status.HTTP_201_CREATED)
async def apply(
    data: BusinessApply,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    existing = await db.execute(select(Business.id).where(Business.owner_id == user.id))
    if existing.first():
        raise HTTPException(status_code=400, detail="You already have a business application")

    slug = await _unique_slug(db, data.name)
    biz = Business(
        owner_id=user.id,
        business_type=data.business_type,
        name=data.name,
        slug=slug,
        country=data.country,
        city=data.city,
        whatsapp=data.whatsapp,
        email=(data.email or "").strip() or None,
        description=data.description,
        logo_url=data.logo_url,
        status="pending",
    )
    db.add(biz)
    
    try:
        await db.commit()
        await db.refresh(biz)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A business with this name or user account already exists. Please try again."
        )

    return {"success": True, "status": biz.status, "slug": biz.slug}


@router.get("/mine")
async def mine(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    biz = (await db.execute(select(Business).where(Business.owner_id == user.id))).scalars().first()
    if not biz:
        return {"business": None}
    return {"business": {**_public(biz), "status": biz.status, "email": biz.email}}


@router.put("/mine")
async def update_mine(
    data: BusinessApply,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    biz = (await db.execute(select(Business).where(Business.owner_id == user.id))).scalars().first()
    if not biz:
        raise HTTPException(status_code=404, detail="No business found")

    # Re-trigger pending status if core identifying info is changed
    if biz.name != data.name or biz.whatsapp != data.whatsapp:
        biz.status = "pending"

    biz.name = data.name
    biz.country = data.country
    biz.city = data.city
    biz.whatsapp = data.whatsapp
    biz.email = (data.email or "").strip() or None
    biz.description = data.description
    biz.logo_url = data.logo_url

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Could not update business details")

    return {"success": True, "status": biz.status}


# ---------- admin routes ----------

@router.get("/admin/list")
async def admin_list(
    status_filter: str = Query("pending", alias="status"),
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if status_filter != "all" and status_filter not in STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status filter. Must be 'all' or one of {list(STATUSES)}")

    q = select(Business, models.User).join(models.User, models.User.id == Business.owner_id).order_by(desc(Business.created_at))
    if status_filter != "all":
        q = q.where(Business.status == status_filter)

    rows = (await db.execute(q)).all()
    return {
        "count": len(rows),
        "businesses": [
            {**_public(b), "status": b.status, "email": b.email, "owner": {"name": u.full_name, "email": u.email, "phone": u.phone}}
            for b, u in rows
        ],
    }


@router.post("/admin/{business_id}/status")
async def admin_set_status(
    business_id: int,
    new_status: str = Query(..., alias="status"),
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if new_status not in STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    biz = (await db.execute(select(Business).where(Business.id == business_id))).scalars().first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")
    biz.status = new_status
    await db.commit()
    return {"success": True, "status": new_status}


# ---------- public routes ----------

@router.get("/")
async def list_businesses(
    business_type: Optional[str] = Query(None, alias="type"),
    country: Optional[str] = None,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Business).where(Business.status == "verified")
    if business_type:
        if business_type not in BUSINESS_TYPES:
            raise HTTPException(status_code=400, detail="Invalid business type filter")
        query = query.where(Business.business_type == business_type)
    if country:
        query = query.where(Business.country.ilike(country))
    if q:
        query = query.where(or_(Business.name.ilike(f"%{q}%"), Business.city.ilike(f"%{q}%")))

    rows = (await db.execute(query.order_by(Business.name).limit(200))).scalars().all()
    return {"count": len(rows), "businesses": [_public(b) for b in rows]}


@router.get("/{slug}")
async def get_business(slug: str, db: AsyncSession = Depends(get_db)):
    biz = (await db.execute(select(Business).where(Business.slug == slug, Business.status == "verified"))).scalars().first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")

    vehicle_rows = (await db.execute(
        select(models.VehicleListing)
        .where(
            models.VehicleListing.is_sold.is_(False),
            or_(
                models.VehicleListing.business_id == biz.id,
                models.VehicleListing.seller_id == biz.owner_id,
            ),
        )
        .order_by(desc(models.VehicleListing.created_at))
        .limit(50)
    )).scalars().all()
    part_rows = (await db.execute(
        select(models.PartListing)
        .where(
            or_(
                models.PartListing.seller_id == biz.owner_id,
                models.PartListing.vendor_id == biz.owner_id,
            ),
            models.PartListing.stock_quantity > 0,
        )
        .order_by(desc(models.PartListing.created_at))
        .limit(100)
    )).scalars().all()

    result = _public(biz)
    result["listings"] = {
        "vehicles": [{
            "type": "vehicle", "id": item.id, "title": item.title,
            "make": item.make, "model": item.model, "year": item.year,
            "price_sll": item.price_sll, "location": item.location,
            "image_url": item.image_url, "condition": item.condition,
            "availability": item.availability,
        } for item in vehicle_rows],
        "parts": [{
            "type": "part", "id": item.id, "name": item.name,
            "category": item.category, "compatible_make": item.compatible_make,
            "compatible_model": item.compatible_model, "price_sll": item.price_sll,
            "location": item.location, "image_url": item.image_url,
            "condition": item.condition, "stock_quantity": item.stock_quantity,
        } for item in part_rows],
    }
    return result
