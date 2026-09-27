from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Optional

from app.db import get_db
from app import models, schemas

router = APIRouter(prefix="/api/vehicles", tags=["Vehicle Listings"])


@router.post("/", response_model=schemas.VehicleResponse)
async def create_vehicle_listing(vehicle: schemas.VehicleCreate, db: AsyncSession = Depends(get_db)):
    new_vehicle = models.VehicleListing(**vehicle.model_dump())
    db.add(new_vehicle)
    await db.commit()
    await db.refresh(new_vehicle)
    return new_vehicle


@router.get("/", response_model=List[schemas.VehicleResponse])
async def search_vehicles(
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    transmission: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    is_sold: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(models.VehicleListing)
    if is_sold is not None:
        query = query.where(models.VehicleListing.is_sold == is_sold)
    else:
        query = query.where(models.VehicleListing.is_sold == False)
    if make:
        query = query.where(models.VehicleListing.make.ilike(f"%{make}%"))
    if model:
        query = query.where(models.VehicleListing.model.ilike(f"%{model}%"))
    if location:
        query = query.where(models.VehicleListing.location.ilike(f"%{location}%"))
    if transmission:
        query = query.where(models.VehicleListing.transmission.ilike(f"%{transmission}%"))
    if min_price is not None:
        query = query.where(models.VehicleListing.price_sll >= min_price)
    if max_price is not None:
        query = query.where(models.VehicleListing.price_sll <= max_price)
    query = query.order_by(models.VehicleListing.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats/count")
async def get_vehicle_stats(db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(models.VehicleListing.id)))
    sold = await db.execute(select(func.count(models.VehicleListing.id)).where(models.VehicleListing.is_sold == True))
    return {"total": total.scalar(), "sold": sold.scalar(), "available": total.scalar() - sold.scalar()}


@router.get("/{vehicle_id}", response_model=schemas.VehicleResponse)
async def get_vehicle(vehicle_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id))
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


@router.put("/{vehicle_id}", response_model=schemas.VehicleResponse)
async def update_vehicle(vehicle_id: int, vehicle: schemas.VehicleCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id))
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


@router.post("/{vehicle_id}/view")
async def track_vehicle_view(vehicle_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id))
    vehicle = result.scalars().first()
    if vehicle:
        vehicle.views = (vehicle.views or 0) + 1
        await db.commit()
    return {"ok": True}


@router.put("/{vehicle_id}/sold")
async def mark_vehicle_sold(vehicle_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id))
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    vehicle.is_sold = True
    await db.commit()
    return {"message": "Vehicle marked as sold", "id": vehicle_id}


@router.delete("/{vehicle_id}")
async def delete_vehicle(vehicle_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id))
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    await db.delete(vehicle)
    await db.commit()
    return {"message": "Vehicle deleted"}
