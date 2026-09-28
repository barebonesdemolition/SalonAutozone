from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timedelta

from app.db import get_db
from app import models, schemas

router = APIRouter(prefix="/api/vehicles", tags=["Vehicles"])


# ============================================================
# SEARCH (paginated, featured first)
# ============================================================
@router.get("/", response_model=List[schemas.VehicleResponse])
async def search_vehicles(
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    year: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(models.VehicleListing)

    if q:
        like = f"%{q}%"
        query = query.where(
            models.VehicleListing.title.ilike(like)
            | models.VehicleListing.make.ilike(like)
            | models.VehicleListing.model.ilike(like)
        )
    if make:
        query = query.where(models.VehicleListing.make.ilike(f"%{make}%"))
    if model:
        query = query.where(models.VehicleListing.model.ilike(f"%{model}%"))
    if year:
        try:
            query = query.where(models.VehicleListing.year == int(year))
        except ValueError:
            pass
    if location:
        query = query.where(models.VehicleListing.location.ilike(f"%{location}%"))

    offset = (page - 1) * limit
    query = (
        query
        .order_by(
            models.VehicleListing.is_featured.desc(),
            models.VehicleListing.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(query)
    return result.scalars().all()


# ============================================================
# CREATE
# ============================================================
@router.post("/", response_model=schemas.VehicleResponse)
async def create_vehicle(
    vehicle: schemas.VehicleCreate,
    db: AsyncSession = Depends(get_db),
):
    new_vehicle = models.VehicleListing(**vehicle.model_dump())
    db.add(new_vehicle)
    await db.commit()
    await db.refresh(new_vehicle)
    return new_vehicle


# ============================================================
# GET ONE
# ============================================================
@router.get("/{vehicle_id}", response_model=schemas.VehicleResponse)
async def get_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


# ============================================================
# UPDATE
# ============================================================
@router.put("/{vehicle_id}", response_model=schemas.VehicleResponse)
async def update_vehicle(
    vehicle_id: int,
    vehicle: schemas.VehicleCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    existing = result.scalars().first()
    if not existing:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    update_data = vehicle.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key == "seller_id":
            continue
        setattr(existing, key, value)

    await db.commit()
    await db.refresh(existing)
    return existing


# ============================================================
# DELETE
# ============================================================
@router.delete("/{vehicle_id}")
async def delete_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    await db.delete(vehicle)
    await db.commit()
    return {"message": "Vehicle deleted"}


# ============================================================
# MARK AS SOLD
# ============================================================
@router.post("/{vehicle_id}/sold")
async def mark_vehicle_sold(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    vehicle.is_sold = True
    await db.commit()
    return {"success": True, "vehicle_id": vehicle.id, "is_sold": True}


# ============================================================
# PROMOTE (FEATURE) A VEHICLE LISTING
# ============================================================
@router.post("/{vehicle_id}/promote")
async def promote_vehicle(
    vehicle_id: int,
    days: int = 7,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    now = datetime.utcnow()
    base = vehicle.featured_until if (vehicle.featured_until and vehicle.featured_until > now) else now
    vehicle.featured_until = base + timedelta(days=days)
    vehicle.is_featured = True

    await db.commit()
    await db.refresh(vehicle)
    return {
        "success": True,
        "vehicle_id": vehicle.id,
        "is_featured": vehicle.is_featured,
        "featured_until": vehicle.featured_until.isoformat(),
    }


@router.post("/{vehicle_id}/unpromote")
async def unpromote_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    vehicle.is_featured = False
    vehicle.featured_until = None
    await db.commit()
    return {"success": True}


# ============================================================
# TRACK VIEW
# ============================================================
@router.post("/{vehicle_id}/view")
async def track_vehicle_view(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    vehicle = result.scalars().first()
    if vehicle:
        vehicle.views = (vehicle.views or 0) + 1
        await db.commit()
    return {"ok": True}
