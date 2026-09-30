from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc, or_
from pydantic import BaseModel, field_validator
from typing import Optional, List

from app.db import get_db
from app import models
from app.auth import get_current_user

router = APIRouter(prefix="/api/my-account", tags=["My Account"])


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
# MY CARS
# ============================================================

@router.get("/my-cars")
async def my_cars(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.VehicleListing)
        .where(models.VehicleListing.seller_id == user.id)
        .order_by(
            models.VehicleListing.is_featured.desc(),
            desc(models.VehicleListing.created_at),
        )
    )
    vehicles = result.scalars().all()

    # Batch inquiry counts (avoids N+1)
    vehicle_ids = [v.id for v in vehicles]
    inquiry_map = {}
    if vehicle_ids:
        inq = await db.execute(
            select(
                models.Inquiry.listing_id,
                func.count(models.Inquiry.id),
            )
            .where(
                models.Inquiry.listing_type == "vehicle",
                models.Inquiry.listing_id.in_(vehicle_ids),
            )
            .group_by(models.Inquiry.listing_id)
        )
        inquiry_map = {row[0]: row[1] for row in inq.all()}

    output = [
        {
            "id": v.id,
            "title": v.title,
            "make": v.make,
            "model": v.model,
            "year": v.year,
            "price_sll": v.price_sll,
            "price_usd": v.price_usd,
            "location": v.location,
            "image_url": v.image_url,
            "is_sold": v.is_sold,
            "inquiry_count": inquiry_map.get(v.id, 0),
            "is_featured": bool(v.is_featured),
            "featured_until": v.featured_until.isoformat() if v.featured_until else None,
            "views": v.views or 0,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in vehicles
    ]
    return {"count": len(output), "vehicles": output}


# ============================================================
# MY PARTS
# ============================================================

@router.get("/my-parts")
async def my_parts(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.PartListing)
        .where(models.PartListing.vendor_id == user.id)
        .order_by(
            models.PartListing.is_featured.desc(),
            desc(models.PartListing.created_at),
        )
    )
    parts = result.scalars().all()

    part_ids = [p.id for p in parts]
    inquiry_map = {}
    if part_ids:
        inq = await db.execute(
            select(
                models.Inquiry.listing_id,
                func.count(models.Inquiry.id),
            )
            .where(
                models.Inquiry.listing_type == "part",
                models.Inquiry.listing_id.in_(part_ids),
            )
            .group_by(models.Inquiry.listing_id)
        )
        inquiry_map = {row[0]: row[1] for row in inq.all()}

    output = [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "compatible_make": p.compatible_make,
            "compatible_model": p.compatible_model,
            "price_sll": p.price_sll,
            "stock_quantity": p.stock_quantity,
            "condition": p.condition,
            "location": p.location,
            "image_url": p.image_url,
            "inquiry_count": inquiry_map.get(p.id, 0),
            "is_featured": bool(p.is_featured),
            "featured_until": p.featured_until.isoformat() if p.featured_until else None,
            "views": p.views or 0,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in parts
    ]
    return {"count": len(output), "parts": output}


# ============================================================
# RECEIVED INQUIRIES
# ============================================================

@router.get("/received-inquiries")
async def received_inquiries(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v_ids = (
        await db.execute(
            select(models.VehicleListing.id).where(
                models.VehicleListing.seller_id == user.id
            )
        )
    ).scalars().all()

    p_ids = (
        await db.execute(
            select(models.PartListing.id).where(
                models.PartListing.vendor_id == user.id
            )
        )
    ).scalars().all()

    if not v_ids and not p_ids:
        return {"count": 0, "inquiries": []}

    conditions = []
    if v_ids:
        conditions.append(
            (models.Inquiry.listing_type == "vehicle")
            & (models.Inquiry.listing_id.in_(v_ids))
        )
    if p_ids:
        conditions.append(
            (models.Inquiry.listing_type == "part")
            & (models.Inquiry.listing_id.in_(p_ids))
        )

    result = await db.execute(
        select(models.Inquiry)
        .where(or_(*conditions))
        .order_by(desc(models.Inquiry.created_at))
    )
    inquiries = result.scalars().all()

    output = [
        {
            "id": i.id,
            "listing_type": i.listing_type,
            "listing_id": i.listing_id,
            "listing_title": i.listing_title,
            "buyer_name": i.buyer_name,
            "buyer_phone": i.buyer_phone,
            "buyer_message": i.buyer_message,
            "status": i.status,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in inquiries
    ]
    return {"count": len(output), "inquiries": output}


# ============================================================
# MY INQUIRIES (SENT)
# ============================================================

@router.get("/my-inquiries")
async def my_inquiries(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.Inquiry)
        .where(models.Inquiry.buyer_phone == user.phone)
        .order_by(desc(models.Inquiry.created_at))
    )
    inquiries = result.scalars().all()

    output = [
        {
            "id": i.id,
            "listing_type": i.listing_type,
            "listing_id": i.listing_id,
            "listing_title": i.listing_title,
            "buyer_message": i.buyer_message,
            "status": i.status,
            "seller_phone": i.seller_phone,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in inquiries
    ]
    return {"count": len(output), "inquiries": output}


# ============================================================
# MY IMPORT REQUESTS
# ============================================================

@router.get("/my-import-requests")
async def my_import_requests(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.ImportRequest)
        .where(models.ImportRequest.customer_phone == user.phone)
        .order_by(desc(models.ImportRequest.created_at))
    )
    requests = result.scalars().all()

    output = [
        {
            "id": r.id,
            "part_name": r.part_name,
            "car_make": r.car_make,
            "car_model": r.car_model,
            "car_year": r.car_year,
            "quantity": r.quantity,
            "budget_sll": r.budget_sll,
            "urgency": r.urgency,
            "status": r.status,
            "quoted_price_sll": r.quoted_price_sll,
            "estimated_days": r.estimated_days,
            "supplier_country": r.supplier_country,
            "admin_notes": r.admin_notes,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in requests
    ]
    return {"count": len(output), "requests": output}


# ============================================================
# STATS
# ============================================================

@router.get("/stats")
async def my_stats(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Parallel-friendly scalar queries
    total_cars = (
        await db.execute(
            select(func.count(models.VehicleListing.id)).where(
                models.VehicleListing.seller_id == user.id
            )
        )
    ).scalar() or 0

    cars_sold = (
        await db.execute(
            select(func.count(models.VehicleListing.id)).where(
                models.VehicleListing.seller_id == user.id,
                models.VehicleListing.is_sold.is_(True),
            )
        )
    ).scalar() or 0

    total_parts = (
        await db.execute(
            select(func.count(models.PartListing.id)).where(
                models.PartListing.vendor_id == user.id
            )
        )
    ).scalar() or 0

    inquiries_sent = (
        await db.execute(
            select(func.count(models.Inquiry.id)).where(
                models.Inquiry.buyer_phone == user.phone
            )
        )
    ).scalar() or 0

    import_requests = (
        await db.execute(
            select(func.count(models.ImportRequest.id)).where(
                models.ImportRequest.customer_phone == user.phone
            )
        )
    ).scalar() or 0

    featured_cars = (
        await db.execute(
            select(func.count(models.VehicleListing.id)).where(
                models.VehicleListing.seller_id == user.id,
                models.VehicleListing.is_featured.is_(True),
            )
        )
    ).scalar() or 0

    featured_parts = (
        await db.execute(
            select(func.count(models.PartListing.id)).where(
                models.PartListing.vendor_id == user.id,
                models.PartListing.is_featured.is_(True),
            )
        )
    ).scalar() or 0

    # Saved count (table may not exist yet)
    saved_count = 0
    try:
        saved_count = (
            await db.execute(
                select(func.count(models.SavedListing.id)).where(
                    models.SavedListing.user_id == user.id
                )
            )
        ).scalar() or 0
    except Exception:
        pass

    # Received inquiries
    v_ids = (
        await db.execute(
            select(models.VehicleListing.id).where(
                models.VehicleListing.seller_id == user.id
            )
        )
    ).scalars().all()
    p_ids = (
        await db.execute(
            select(models.PartListing.id).where(
                models.PartListing.vendor_id == user.id
            )
        )
    ).scalars().all()

    received = 0
    if v_ids:
        received += (
            await db.execute(
                select(func.count(models.Inquiry.id)).where(
                    models.Inquiry.listing_type == "vehicle",
                    models.Inquiry.listing_id.in_(v_ids),
                )
            )
        ).scalar() or 0
    if p_ids:
        received += (
            await db.execute(
                select(func.count(models.Inquiry.id)).where(
                    models.Inquiry.listing_type == "part",
                    models.Inquiry.listing_id.in_(p_ids),
                )
            )
        ).scalar() or 0

    return {
        "total_cars": total_cars,
        "cars_sold": cars_sold,
        "total_parts": total_parts,
        "total_inquiries_sent": inquiries_sent,
        "total_inquiries_received": received,
        "total_import_requests": import_requests,
        "featured_parts": featured_parts,
        "featured_cars": featured_cars,
        "saved_items": saved_count,
    }


# ============================================================
# SAVED LISTINGS
# ============================================================

@router.get("/saved")
async def list_saved(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(models.SavedListing)
            .where(models.SavedListing.user_id == user.id)
            .order_by(desc(models.SavedListing.created_at))
        )
        items = result.scalars().all()
    except Exception:
        return {
            "count": 0,
            "items": [],
            "warning": "saved_listings table not created yet",
        }

    output = [
        {
            "id": s.id,
            "listing_type": s.listing_type,
            "listing_id": s.listing_id,
            "listing_title": s.listing_title,
            "listing_price_sll": s.listing_price_sll,
            "listing_image_url": s.listing_image_url,
            "notes": s.notes,
            "created_at": s.created_at.isoformat() if s.created_at else None,
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
    # Already saved?
    existing = await db.execute(
        select(models.SavedListing).where(
            models.SavedListing.user_id == user.id,
            models.SavedListing.listing_type == item.listing_type,
            models.SavedListing.listing_id == item.listing_id,
        )
    )
    if existing.scalars().first():
        return {"success": True, "already_saved": True}

    title = price = image = None
    if item.listing_type == "part":
        r = await db.execute(
            select(models.PartListing).where(models.PartListing.id == item.listing_id)
        )
        p = r.scalars().first()
        if not p:
            raise HTTPException(status_code=404, detail="Part not found")
        title, price, image = p.name, p.price_sll, p.image_url
    else:
        r = await db.execute(
            select(models.VehicleListing).where(
                models.VehicleListing.id == item.listing_id
            )
        )
        v = r.scalars().first()
        if not v:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        title, price, image = v.title, v.price_sll, v.image_url

    saved = models.SavedListing(
        user_id=user.id,
        listing_type=item.listing_type,
        listing_id=item.listing_id,
        listing_title=title,
        listing_price_sll=price,
        listing_image_url=image,
        notes=item.notes,
    )
    db.add(saved)
    await db.commit()
    await db.refresh(saved)
    return {"success": True, "id": saved.id}


@router.delete("/saved/{saved_id}")
async def unsave_listing(
    saved_id: int,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.SavedListing).where(
            models.SavedListing.id == saved_id,
            models.SavedListing.user_id == user.id,
        )
    )
    item = result.scalars().first()
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
    vehicle_id: int,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if vehicle.seller_id != user.id:
        raise HTTPException(
            status_code=403, detail="You can only delete your own listings"
        )

    await db.delete(vehicle)
    await db.commit()
    return {"message": "Deleted", "id": vehicle_id}


@router.delete("/part/{part_id}")
async def delete_my_part(
    part_id: int,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.PartListing).where(models.PartListing.id == part_id)
    )
    part = result.scalars().first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if part.vendor_id != user.id:
        raise HTTPException(
            status_code=403, detail="You can only delete your own listings"
        )

    await db.delete(part)
    await db.commit()
    return {"message": "Deleted", "id": part_id}
