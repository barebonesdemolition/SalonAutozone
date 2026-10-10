import re

from app.auth import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update
from pydantic import BaseModel, field_validator
from typing import List, Optional
from datetime import datetime
from jose import jwt, JWTError

from app.db import get_db
from app import models
from app.config import get_settings

router = APIRouter(prefix="/api/garage", tags=["My Garage"])
settings = get_settings()


# ============================================================
# SCHEMAS
# ============================================================
class GarageCar(BaseModel):
    make: str
    model: str
    year: int
    vin: Optional[str] = None
    nickname: Optional[str] = None
    is_primary: bool = False

    @field_validator("vin")
    @classmethod
    def validate_vin(cls, value):
        if value is None or not value.strip():
            return None
        vin = value.strip().upper()
        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            raise ValueError("VIN must be 17 valid characters; I, O, and Q are not allowed.")
        return vin


class GarageCarResponse(BaseModel):
    id: int | str  # the garage table uses integer ids; str kept for older rows
    make: str
    model: str
    year: int
    vin: Optional[str] = None
    nickname: Optional[str] = None
    is_primary: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================================
# AUTH HELPER
# ============================================================
# get_current_user is imported from app.auth above

# ============================================================
# LIST MY CARS
# ============================================================
@router.get("/", response_model=List[GarageCarResponse])
async def list_my_cars(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.Garage)
        .where(models.Garage.user_id == user.id)
        .order_by(models.Garage.is_primary.desc(), models.Garage.created_at.desc())
    )
    return result.scalars().all()


# ============================================================
# ADD A CAR
# ============================================================
@router.post("/", response_model=GarageCarResponse)
async def add_car(
    car: GarageCar,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # If this is the user's first car, make it primary
    existing = await db.execute(
        select(models.Garage).where(models.Garage.user_id == user.id)
    )
    existing_cars = existing.scalars().all()
    is_first = len(existing_cars) == 0

    # If this car is marked primary, unset others
    if car.is_primary or is_first:
        await db.execute(
            update(models.Garage)
            .where(models.Garage.user_id == user.id)
            .values(is_primary=False)
        )

    new_car = models.Garage(
        user_id=user.id,
        make=car.make.strip(),
        model=car.model.strip(),
        year=car.year,
        vin=car.vin,
        nickname=(car.nickname or '').strip() or None,
        is_primary=car.is_primary or is_first,
    )
    db.add(new_car)
    await db.commit()
    await db.refresh(new_car)
    return new_car


# ============================================================
# DELETE A CAR
# ============================================================
@router.delete("/{car_id}")
async def delete_car(
    car_id: int,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.Garage).where(
            models.Garage.id == car_id,
            models.Garage.user_id == user.id,
        )
    )
    car = result.scalars().first()
    if not car:
        raise HTTPException(status_code=404, detail="Car not found")

    was_primary = car.is_primary
    await db.delete(car)
    await db.commit()

    # If we just deleted the primary car, promote the newest remaining
    if was_primary:
        remaining = await db.execute(
            select(models.Garage)
            .where(models.Garage.user_id == user.id)
            .order_by(models.Garage.created_at.desc())
            .limit(1)
        )
        next_car = remaining.scalars().first()
        if next_car:
            next_car.is_primary = True
            await db.commit()

    return {"success": True}


# ============================================================
# SET AS PRIMARY
# ============================================================
@router.put("/{car_id}/primary", response_model=GarageCarResponse)
async def set_primary(
    car_id: int,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Unset all others
    await db.execute(
        update(models.Garage)
        .where(models.Garage.user_id == user.id)
        .values(is_primary=False)
    )

    # Set this one
    result = await db.execute(
        select(models.Garage).where(
            models.Garage.id == car_id,
            models.Garage.user_id == user.id,
        )
    )
    car = result.scalars().first()
    if not car:
        raise HTTPException(status_code=404, detail="Car not found")

    car.is_primary = True
    await db.commit()
    await db.refresh(car)
    return car


# ============================================================
# UPDATE A CAR (edit nickname / make / model / year)
# ============================================================
@router.put("/{car_id}", response_model=GarageCarResponse)
async def update_car(
    car_id: int,
    car: GarageCar,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.Garage).where(
            models.Garage.id == car_id,
            models.Garage.user_id == user.id,
        )
    )
    existing = result.scalars().first()
    if not existing:
        raise HTTPException(status_code=404, detail="Car not found")

    existing.make = car.make.strip()
    existing.model = car.model.strip()
    existing.year = car.year
    existing.vin = car.vin
    existing.nickname = (car.nickname or '').strip() or None
    if car.is_primary and not existing.is_primary:
        await db.execute(
            update(models.Garage).where(models.Garage.user_id == user.id).values(is_primary=False)
        )
        existing.is_primary = True

    await db.commit()
    await db.refresh(existing)
    return existing
