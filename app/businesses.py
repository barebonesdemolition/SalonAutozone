"""Business accounts: local parts stores, car dealerships, shippers/importers and wholesalers abroad.

Step 1: apply, public storefront profile, admin verification. Listings get linked to a business in step 2.
"""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app import models
from app.auth import get_current_user
from app.db import Base, get_db
from app.routers.admin import require_admin

router = APIRouter(prefix="/api/businesses", tags=["Businesses"])

BUSINESS_TYPES = {
    "store": "Local parts store",
    "dealership": "Car dealership",
    "shipper": "Shipper / importer",
    "wholesaler": "Wholesaler (abroad)",
}
STATUSES = {"pending", "verified", "rejected", "suspended"}


class Business(Base):
    __tablename__ = "businesses"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    business_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, unique=True, index=True)
    country = Column(String, nullable=False, default="Sierra Leone")
    city = Column(String, nullable=False)
    whatsapp = Column(String, nullable=False)
    email = Column(String)
    description = Column(Text)
    logo_url = Column(String)
    status = Column(String, nullable=False, default="pending", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


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
        "id": b.id, "slug": b.slug, "business_type": b.business_type,
        "type_label": BUSINESS_TYPES.get(b.business_type, b.business_type),
        "name": b.name, "country": b.country, "city": b.city, "whatsapp": b.whatsapp,
        "description": b.description, "logo_url": b.logo_url,
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
        v = v.strip()
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
            raise ValueError("Logo must be an https link")
        return v


# ---------- owner routes ----------

@router.post("/apply", status_code=status.HTTP_201_CREATED)
async def apply(data: BusinessApply, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(Business.id).where(Business.owner_id == user.id))).first():
        raise HTTPException(status_code=400, detail="You already have a business application")
    biz = Business(
        owner_id=user.id, business_type=data.business_type, name=data.name,
        slug=await _unique_slug(db, data.name), country=data.country, city=data.city,
        whatsapp=data.whatsapp, email=(data.email or "").strip() or None,
        description=data.description, logo_url=data.logo_url, status="pending",
    )
    db.add(biz)
    await db.commit()
    await db.refresh(biz)
    return {"success": True, "status": biz.status, "slug": biz.slug}


@router.get("/mine")
async def mine(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    biz = (await db.execute(select(Business).where(Business.owner_id == user.id))).scalars().first()
    if not biz:
        return {"business": None}
    return {"business": {**_public(biz), "status": biz.status, "email": biz.email}}


@router.put("/mine")
async def update_mine(data: BusinessApply, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    biz = (await db.execute(select(Business).where(Business.owner_id == user.id))).scalars().first()
    if not biz:
        raise HTTPException(status_code=404, detail="No business found")
    # type and slug cannot be changed by the owner; status is controlled by admins
    biz.name, biz.country, biz.city, biz.whatsapp = data.name, data.country, data.city, data.whatsapp
    biz.email, biz.description, biz.logo_url = (data.email or "").strip() or None, data.description, data.logo_url
    await db.commit()
    return {"success": True}


# ---------- admin routes (declared before /{slug}) ----------

@router.get("/admin/list")
async def admin_list(
    status_filter: str = Query("pending", alias="status"),
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    q = select(Business, models.User).join(models.User, models.User.id == Business.owner_id).order_by(desc(Business.created_at))
    if status_filter != "all":
        q = q.where(Business.status == status_filter)
    rows = (await db.execute(q)).all()
    return {
        "count": len(rows),
        "businesses": [
            {**_public(b), "status": b.status, "email": b.email,
             "owner": {"name": u.full_name, "email": u.email, "phone": u.phone}}
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
    return _public(biz)
