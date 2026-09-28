from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update
from pydantic import BaseModel
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
    nickname: Optional[str] = None
    is_primary: bool = False


class GarageCarResponse(BaseModel):
    id: int
    make: str
    model: str
    year: int
    nickname: Optional[str] = None
    is_primary: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================================
# AUTH HELPER
# ============================================================
async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

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
    existing.nickname = (car.nickname or '').strip() or None

    await db.commit()
    await db.refresh(existing)
    return existing
