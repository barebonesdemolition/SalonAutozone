"""Public business storefront endpoints."""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db import get_db
from app import models

router = APIRouter(prefix="/api/businesses", tags=["Businesses"])


@router.get("/{slug}")
async def get_storefront(slug: str, db: AsyncSession = Depends(get_db)):
    """Return a public business profile and its listings."""
    result = await db.execute(select(models.Business).where(models.Business.slug == slug))
    biz = result.scalars().first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")

    # Listings owned by this business
    vres = await db.execute(
        select(models.VehicleListing)
        .where(models.VehicleListing.business_id == biz.id)
        .order_by(models.VehicleListing.created_at.desc())
        .limit(50)
    )
    vehicles = vres.scalars().all()

    def vdict(v):
        return {
            "id": v.id,
            "title": v.title,
            "make": v.make,
            "model": v.model,
            "year": v.year,
            "price_sll": v.price_sll,
            "price_usd": getattr(v, "price_usd", None),
            "location": v.location,
            "image_url": getattr(v, "image_url", None),
            "condition": getattr(v, "condition", "used"),
            "availability": getattr(v, "availability", "in_stock"),
            "mileage_km": getattr(v, "mileage_km", None),
        }

    return {
        "business": {
            "id": biz.id,
            "name": biz.name,
            "slug": biz.slug,
            "type": getattr(biz, "business_type", None),
            "status": getattr(biz, "status", None),
            "city": getattr(biz, "city", None),
            "country": getattr(biz, "country", None),
            "whatsapp": getattr(biz, "whatsapp", None),
            "email": getattr(biz, "email", None),
            "description": getattr(biz, "description", None),
            "logo_url": getattr(biz, "logo_url", None),
            "subscription_tier": getattr(biz, "subscription_tier", None),
            "created_at": str(biz.created_at) if getattr(biz, "created_at", None) else None,
            "verified": getattr(biz, "status", None) == "verified",
        },
        "vehicles": [vdict(v) for v in vehicles],
        "vehicle_count": len(vehicles),
    }
