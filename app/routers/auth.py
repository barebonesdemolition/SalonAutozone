# app/routers/auth.py
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import func
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

ALLOWED_ROLES = {"buyer", "seller", "vendor"}  # "admin" can never be self-assigned


# ---------- Helpers ----------

def normalize_phone(raw: str) -> str:
    """Store phones in one format. Sierra Leone numbers (8 digits, or 9 with a leading 0) become +232XXXXXXXX."""
    s = (raw or "").strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 9 and digits.startswith("0"):
        digits = "232" + digits[1:]
    elif len(digits) == 8:
        digits = "232" + digits
    return "+" + digits


def _phone_key(phone: str) -> str:
    """Last 8 digits: the same rule my_account.py uses, so formats can't create duplicate identities."""
    return re.sub(r"\D", "", phone or "")[-8:]


# Simple login throttle: 5 failures per email in 15 minutes (per server process).
_FAILS: dict = {}
_WINDOW = 15 * 60
_MAX_FAILS = 5


def _throttle_check(key: str) -> None:
    now = time.time()
    recent = [t for t in _FAILS.get(key, []) if now - t < _WINDOW]
    _FAILS[key] = recent
    if len(recent) >= _MAX_FAILS:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")


def _throttle_fail(key: str) -> None:
    _FAILS.setdefault(key, []).append(time.time())


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
    def phone_valid(cls, v: str) -> str:
        if len(re.sub(r"\D", "", v)) < 8:
            raise ValueError("Phone number looks too short")
        return v.strip()

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password is too long (72 characters maximum)")
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
    email = user.email.lower().strip()
    phone = normalize_phone(user.phone)

    if (await db.execute(select(models.User).where(models.User.email == email))).scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Same number in any format (spaces, +232, leading 0) counts as the same number
    phone_clash = await db.execute(
        select(models.User.id).where(
            func.right(func.regexp_replace(models.User.phone, r"[^0-9]", "", "g"), 8) == _phone_key(phone)
        )
    )
    if phone_clash.first():
        raise HTTPException(status_code=400, detail="Phone number already registered")

    roles = None
    if user.roles:
        picked = [r.strip().lower() for r in user.roles.split(",") if r.strip().lower() in ALLOWED_ROLES]
        roles = ",".join(dict.fromkeys(picked)) or None

    new_user = models.User(
        full_name=user.full_name.strip(),
        email=email,
        phone=phone,
        hashed_password=hash_password(user.password),
        is_vendor=user.is_vendor,
        roles=roles,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@router.post("/login", response_model=Token)
async def login_user(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    email = credentials.email.lower().strip()
    _throttle_check(email)

    user = (await db.execute(select(models.User).where(models.User.email == email))).scalars().first()

    if not user or not verify_password(credentials.password, user.hashed_password):
        _throttle_fail(email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if getattr(user, "is_active", True) is False:
        raise HTTPException(status_code=403, detail="This account has been disabled")

    _FAILS.pop(email, None)
    token = create_access_token(data={"sub": str(user.id), "email": user.email})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def read_current_user(user: models.User = Depends(get_current_user)):
    return user
