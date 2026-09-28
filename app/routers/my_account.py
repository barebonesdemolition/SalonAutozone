from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc, or_
from pydantic import BaseModel
from typing import Optional
from jose import jwt, JWTError

from app.db import get_db
from app import models
from app.config import get_settings

router = APIRouter(prefix="/api/my-account", tags=["My Account"])
settings = get_settings()


# ============================================================
# AUTH
# ============================================================
async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Login required")
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


class SavedListingCreate(BaseModel):
    listing_type: str          # "part" or "vehicle"
    listing_id: int
    notes: Optional[str] = None


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
        .order_by(models.VehicleListing.is_featured.desc(), desc(models.VehicleListing.created_at))
    )
    vehicles = result.scalars().all()
    output = []
    for v in vehicles:
        inq_result = await db.execute(
            select(func.count(models.Inquiry.id))
            .where(models.Inquiry.listing_type == "vehicle")
            .where(models.Inquiry.listing_id == v.id)
        )
        inquiry_count = inq_result.scalar() or 0
        output.append({
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
            "inquiry_count": inquiry_count,
            "is_featured": bool(v.is_featured),
            "featured_until": v.featured_until.isoformat() if v.featured_until else None,
            "views": v.views or 0,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        })
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
        .order_by(models.PartListing.is_featured.desc(), desc(models.PartListing.created_at))
    )
    parts = result.scalars().all()
    output = []
    for p in parts:
        inq_result = await db.execute(
            select(func.count(models.Inquiry.id))
            .where(models.Inquiry.listing_type == "part")
            .where(models.Inquiry.listing_id == p.id)
        )
        inquiry_count = inq_result.scalar() or 0
        output.append({
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
            "inquiry_count": inquiry_count,
            "is_featured": bool(p.is_featured),
            "featured_until": p.featured_until.isoformat() if p.featured_until else None,
            "views": p.views or 0,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return {"count": len(output), "parts": output}


# ============================================================
# RECEIVED INQUIRIES
# ============================================================
@router.get("/received-inquiries")
async def received_inquiries(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v_result = await db.execute(
        select(models.VehicleListing.id).where(models.VehicleListing.seller_id == user.id)
    )
    vehicle_ids = [r for r in v_result.scalars().all()]

    p_result = await db.execute(
        select(models.PartListing.id).where(models.PartListing.vendor_id == user.id)
    )
    part_ids = [r for r in p_result.scalars().all()]

    if not vehicle_ids and not part_ids:
        return {"count": 0, "inquiries": []}

    conditions = []
    if vehicle_ids:
        conditions.append(
            (models.Inquiry.listing_type == "vehicle") & (models.Inquiry.listing_id.in_(vehicle_ids))
        )
    if part_ids:
        conditions.append(
            (models.Inquiry.listing_type == "part") & (models.Inquiry.listing_id.in_(part_ids))
        )

    query = select(models.Inquiry).where(or_(*conditions)).order_by(desc(models.Inquiry.created_at))
    result = await db.execute(query)
    inquiries = result.scalars().all()

    output = []
    for i in inquiries:
        output.append({
            "id": i.id,
            "listing_type": i.listing_type,
            "listing_id": i.listing_id,
            "listing_title": i.listing_title,
            "buyer_name": i.buyer_name,
            "buyer_phone": i.buyer_phone,
            "buyer_message": i.buyer_message,
            "status": i.status,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        })
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
    output = []
    for i in inquiries:
        output.append({
            "id": i.id,
            "listing_type": i.listing_type,
            "listing_id": i.listing_id,
            "listing_title": i.listing_title,
            "buyer_message": i.buyer_message,
            "status": i.status,
            "seller_phone": i.seller_phone,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        })
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
    output = []
    for r in requests:
        output.append({
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
        })
    return {"count": len(output), "requests": output}


# ============================================================
# STATS (with featured + saved counts)
# ============================================================
@router.get("/stats")
async def my_stats(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cars = await db.execute(
        select(func.count(models.VehicleListing.id)).where(models.VehicleListing.seller_id == user.id)
    )
    sold = await db.execute(
        select(func.count(models.VehicleListing.id))
        .where(models.VehicleListing.seller_id == user.id)
        .where(models.VehicleListing.is_sold == True)
    )
    parts = await db.execute(
        select(func.count(models.PartListing.id)).where(models.PartListing.vendor_id == user.id)
    )
    my_inq = await db.execute(
        select(func.count(models.Inquiry.id)).where(models.Inquiry.buyer_phone == user.phone)
    )
    imports = await db.execute(
        select(func.count(models.ImportRequest.id)).where(models.ImportRequest.customer_phone == user.phone)
    )

    # Featured counts
    featured_parts = (await db.execute(
        select(func.count(models.PartListing.id)).where(
            models.PartListing.vendor_id == user.id,
            models.PartListing.is_featured == True,
        )
    )).scalar() or 0

    featured_cars = (await db.execute(
        select(func.count(models.VehicleListing.id)).where(
            models.VehicleListing.seller_id == user.id,
            models.VehicleListing.is_featured == True,
        )
    )).scalar() or 0

    # Saved items count (safe — table might not exist yet)
    saved_count = 0
    try:
        saved_count = (await db.execute(
            select(func.count(models.SavedListing.id)).where(models.SavedListing.user_id == user.id)
        )).scalar() or 0
    except Exception:
        pass

    # Received inquiries
    v_result = await db.execute(
        select(models.VehicleListing.id).where(models.VehicleListing.seller_id == user.id)
    )
    vehicle_ids = [r for r in v_result.scalars().all()]
    received = 0
    if vehicle_ids:
        r = await db.execute(
            select(func.count(models.Inquiry.id))
            .where(models.Inquiry.listing_type == "vehicle")
            .where(models.Inquiry.listing_id.in_(vehicle_ids))
        )
        received += r.scalar() or 0

    p_result = await db.execute(
        select(models.PartListing.id).where(models.PartListing.vendor_id == user.id)
    )
    part_ids = [r for r in p_result.scalars().all()]
    if part_ids:
        r = await db.execute(
            select(func.count(models.Inquiry.id))
            .where(models.Inquiry.listing_type == "part")
            .where(models.Inquiry.listing_id.in_(part_ids))
        )
        received += r.scalar() or 0

    return {
        "total_cars": cars.scalar() or 0,
        "cars_sold": sold.scalar() or 0,
        "total_parts": parts.scalar() or 0,
        "total_inquiries_sent": my_inq.scalar() or 0,
        "total_inquiries_received": received,
        "total_import_requests": imports.scalar() or 0,
        "featured_parts": featured_parts,
        "featured_cars": featured_cars,
        "saved_items": saved_count,
    }


# ============================================================
# SAVED LISTINGS (wishlist / items bought)
# ============================================================
@router.get("/saved")
async def list_saved(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all items the user has saved for later."""
    try:
        result = await db.execute(
            select(models.SavedListing)
            .where(models.SavedListing.user_id == user.id)
            .order_by(desc(models.SavedListing.created_at))
        )
        items = result.scalars().all()
    except Exception:
        # Table doesn't exist yet
        return {"count": 0, "items": [], "warning": "saved_listings table not created yet"}

    output = []
    for s in items:
        output.append({
            "id": s.id,
            "listing_type": s.listing_type,
            "listing_id": s.listing_id,
            "listing_title": s.listing_title,
            "listing_price_sll": s.listing_price_sll,
            "listing_image_url": s.listing_image_url,
            "notes": s.notes,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
    return {"count": len(output), "items": output}


@router.post("/saved")
async def save_listing(
    item: SavedListingCreate,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save a listing to the user's wishlist."""
    if item.listing_type not in ("part", "vehicle"):
        raise HTTPException(status_code=400, detail="listing_type must be 'part' or 'vehicle'")

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

    # Snapshot title/price/image from the actual listing
    title = None
    price = None
    image = None
    if item.listing_type == "part":
        r = await db.execute(
            select(models.PartListing).where(models.PartListing.id == item.listing_id)
        )
        p = r.scalars().first()
        if p:
            title = p.name
            price = p.price_sll
            image = p.image_url
    else:
        r = await db.execute(
            select(models.VehicleListing).where(models.VehicleListing.id == item.listing_id)
        )
        v = r.scalars().first()
        if v:
            title = v.title
            price = v.price_sll
            image = v.image_url

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
    """Remove a listing from the user's wishlist."""
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
        raise HTTPException(status_code=403, detail="You can only delete your own listings")
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
        raise HTTPException(status_code=403, detail="You can only delete your own listings")
    await db.delete(part)
    await db.commit()
    return {"message": "Deleted", "id": part_id}
