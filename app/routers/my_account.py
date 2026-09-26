"""
Buyer & Seller Dashboard Endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc, or_
from typing import Optional, List
from jose import jwt, JWTError

from app.db import get_db
from app import models
from app.config import get_settings

router = APIRouter(prefix="/api/my-account", tags=["My Account"])
settings = get_settings()


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Require login."""
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


@router.get("/my-cars")
async def my_cars(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all cars listed by the current user, with inquiry counts."""
    # Get user's vehicles
    result = await db.execute(
        select(models.VehicleListing)
        .where(models.VehicleListing.seller_id == user.id)
        .order_by(desc(models.VehicleListing.created_at))
    )
    vehicles = result.scalars().all()
    
    # For each vehicle, count inquiries
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
            "created_at": v.created_at.isoformat() if v.created_at else None,
        })
    
    return {"count": len(output), "vehicles": output}


@router.get("/my-parts")
async def my_parts(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all parts listed by the current user."""
    result = await db.execute(
        select(models.PartListing)
        .where(models.PartListing.vendor_id == user.id)
        .order_by(desc(models.PartListing.created_at))
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
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    
    return {"count": len(output), "parts": output}


@router.get("/my-inquiries")
async def my_inquiries(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all inquiries the user has SENT as a buyer."""
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


@router.get("/my-import-requests")
async def my_import_requests(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all import requests the user has submitted."""
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


@router.get("/stats")
async def my_stats(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get quick stats for the user's dashboard."""
    # My cars
    cars = await db.execute(
        select(func.count(models.VehicleListing.id))
        .where(models.VehicleListing.seller_id == user.id)
    )
    # My cars sold
    sold = await db.execute(
        select(func.count(models.VehicleListing.id))
        .where(models.VehicleListing.seller_id == user.id)
        .where(models.VehicleListing.is_sold == True)
    )
    # My parts
    parts = await db.execute(
        select(func.count(models.PartListing.id))
        .where(models.PartListing.vendor_id == user.id)
    )
    # My inquiries (sent)
    my_inq = await db.execute(
        select(func.count(models.Inquiry.id))
        .where(models.Inquiry.buyer_phone == user.phone)
    )
    # Inquiries received (on my listings)
    received = 0
    my_cars_result = await db.execute(
        select(models.VehicleListing.id).where(models.VehicleListing.seller_id == user.id)
    )
    car_ids = [r for r in my_cars_result.scalars().all()]
    if car_ids:
        inq_count = await db.execute(
            select(func.count(models.Inquiry.id))
            .where(models.Inquiry.listing_type == "vehicle")
            .where(models.Inquiry.listing_id.in_(car_ids))
        )
        received += inq_count.scalar() or 0
    
    # My import requests
    imports = await db.execute(
        select(func.count(models.ImportRequest.id))
        .where(models.ImportRequest.customer_phone == user.phone)
    )
    
    return {
        "total_cars": cars.scalar() or 0,
        "cars_sold": sold.scalar() or 0,
        "total_parts": parts.scalar() or 0,
        "total_inquiries_sent": my_inq.scalar() or 0,
        "total_inquiries_received": received,
        "total_import_requests": imports.scalar() or 0,
    }


@router.delete("/vehicle/{vehicle_id}")
async def delete_my_vehicle(
    vehicle_id: int,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a vehicle listing (only if user owns it)."""
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
    """Delete a part listing."""
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
