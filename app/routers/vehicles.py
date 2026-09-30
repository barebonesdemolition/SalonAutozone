from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_, update
from typing import List, Optional
from datetime import datetime, timedelta

from app.db import get_db
from app import models, schemas
from app.auth import get_current_user, get_current_admin

router = APIRouter(prefix="/api/vehicles", tags=["Vehicles"])

# Fields a seller must never set themselves (payment/ownership/stats controlled by the server)
PROTECTED_FIELDS = {"seller_id", "is_featured", "featured_until", "views", "is_sold"}


# ============================================================
# Helpers
# ============================================================

async def _get_vehicle_or_404(db: AsyncSession, vehicle_id: int) -> models.VehicleListing:
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    return vehicle


def _ensure_owner_or_admin(vehicle: models.VehicleListing, user) -> None:
    """Raise 403 unless the user owns the listing or is an admin."""
    is_owner = getattr(vehicle, "seller_id", None) == user.id
    is_admin = getattr(user, "is_admin", False)
    if not (is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this listing",
        )


async def _expire_featured(db: AsyncSession) -> None:
    """Turn off 'featured' on listings whose paid period has ended."""
    await db.execute(
        update(models.VehicleListing)
        .where(
            models.VehicleListing.is_featured == True,  # noqa: E712
            models.VehicleListing.featured_until != None,  # noqa: E711
            models.VehicleListing.featured_until < datetime.utcnow(),
        )
        .values(is_featured=False, featured_until=None)
    )
    await db.commit()


# ============================================================
# SEARCH (paginated, featured first, unsold by default)
# ============================================================

@router.get("/", response_model=List[schemas.VehicleResponse])
async def search_vehicles(
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    year: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    sold: Optional[bool] = Query(False, description="False (default) hides sold cars; true shows only sold"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    await _expire_featured(db)
    query = select(models.VehicleListing)

    if q:
        like = f"%{q}%"
        query = query.where(
            or_(
                models.VehicleListing.title.ilike(like),
                models.VehicleListing.make.ilike(like),
                models.VehicleListing.model.ilike(like),
            )
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
    if sold is not None:
        query = query.where(models.VehicleListing.is_sold == sold)

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

@router.post("/", response_model=schemas.VehicleResponse, status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    vehicle: schemas.VehicleCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    data = vehicle.model_dump(exclude=PROTECTED_FIELDS, exclude_unset=True)
    # Ownership always comes from the login token, never from the client
    data["seller_id"] = current_user.id

    new_vehicle = models.VehicleListing(**data)
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
    return await _get_vehicle_or_404(db, vehicle_id)


# ============================================================
# UPDATE
# ============================================================

@router.put("/{vehicle_id}", response_model=schemas.VehicleResponse)
async def update_vehicle(
    vehicle_id: int,
    vehicle: schemas.VehicleCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    existing = await _get_vehicle_or_404(db, vehicle_id)
    _ensure_owner_or_admin(existing, current_user)

    update_data = vehicle.model_dump(exclude=PROTECTED_FIELDS, exclude_unset=True)
    for key, value in update_data.items():
        setattr(existing, key, value)

    await db.commit()
    await db.refresh(existing)
    return existing


# ============================================================
# DELETE
# ============================================================

@router.delete("/{vehicle_id}", status_code=status.HTTP_200_OK)
async def delete_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    vehicle = await _get_vehicle_or_404(db, vehicle_id)
    _ensure_owner_or_admin(vehicle, current_user)

    await db.delete(vehicle)
    await db.commit()
    return {"message": "Vehicle deleted", "vehicle_id": vehicle_id}


# ============================================================
# MARK AS SOLD
# ============================================================

@router.post("/{vehicle_id}/sold")
async def mark_vehicle_sold(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    vehicle = await _get_vehicle_or_404(db, vehicle_id)
    _ensure_owner_or_admin(vehicle, current_user)

    vehicle.is_sold = True
    await db.commit()
    return {"success": True, "vehicle_id": vehicle.id, "is_sold": True}


# ============================================================
# PROMOTE (ADMIN ONLY, after payment is confirmed) / UNPROMOTE
# ============================================================

@router.post("/{vehicle_id}/promote")
async def promote_vehicle(
    vehicle_id: int,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Feature a listing. Admin only: call this once the Orange Money payment has arrived."""
    vehicle = await _get_vehicle_or_404(db, vehicle_id)

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
        "featured_until": vehicle.featured_until.isoformat() if vehicle.featured_until else None,
    }


@router.post("/{vehicle_id}/unpromote")
async def unpromote_vehicle(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    vehicle = await _get_vehicle_or_404(db, vehicle_id)
    _ensure_owner_or_admin(vehicle, current_user)

    vehicle.is_featured = False
    vehicle.featured_until = None
    await db.commit()
    return {"success": True, "vehicle_id": vehicle.id, "is_featured": False}


# ============================================================
# TRACK VIEW (public – no auth needed)
# ============================================================

@router.post("/{vehicle_id}/view")
async def track_vehicle_view(
    vehicle_id: int,
    db: AsyncSession = Depends(get_db),
):
    vehicle = await _get_vehicle_or_404(db, vehicle_id)
    vehicle.views = (vehicle.views or 0) + 1
    await db.commit()
    return {"ok": True}
