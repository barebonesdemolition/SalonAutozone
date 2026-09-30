# app/routers/auth.py
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db import get_db
from app import models
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# ---------- Schemas ----------

class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    phone: str
    password: str
    is_vendor: bool = False
    roles: Optional[str] = None  # e.g. "buyer,seller,vendor"

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Full name must be at least 2 characters")
        return v

    @field_validator("phone")
    @classmethod
    def phone_not_empty(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 8:
            raise ValueError("Phone number looks too short")
        return v

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    full_name: str
    email: str
    phone: str
    is_vendor: bool
    roles: Optional[str] = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- Routes ----------

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    # Email uniqueness
    existing_email = await db.execute(
        select(models.User).where(models.User.email == user.email.lower().strip())
    )
    if existing_email.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Phone uniqueness
    existing_phone = await db.execute(
        select(models.User).where(models.User.phone == user.phone.strip())
    )
    if existing_phone.scalars().first():
        raise HTTPException(status_code=400, detail="Phone number already registered")

    new_user = models.User(
        full_name=user.full_name.strip(),
        email=user.email.lower().strip(),
        phone=user.phone.strip(),
        hashed_password=hash_password(user.password),
        is_vendor=user.is_vendor,
        roles=user.roles,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@router.post("/login", response_model=Token)
async def login_user(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.User).where(
            models.User.email == credentials.email.lower().strip()
        )
    )
    user = result.scalars().first()

    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        data={"sub": str(user.id), "email": user.email}
    )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def read_current_user(user: models.User = Depends(get_current_user)):
    return user
