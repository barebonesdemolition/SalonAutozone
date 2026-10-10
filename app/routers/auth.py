# app/routers/auth.py
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db import get_db
from app import models
# One place for token/password logic. These names are re-exported for older imports.
from app.auth import (  # noqa: F401
    ADMIN_ROLES,
    SECRET_KEY,
    create_access_token,
    decode_access_token,
    get_current_active_user,
    get_current_admin,
    get_current_user,
    hash_password,
    oauth2_scheme,
    pwd_context,
    require_role,
    user_has_role,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.get("/health")
async def auth_health():
    return {"status": "ok"}


class LoginRequest(BaseModel):
    phone: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=30)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    account_type: Literal["buyer", "seller", "business"] = "buyer"


def _user_payload(user: models.User) -> dict:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "phone": user.phone,
        "email": user.email,
        "is_vendor": user.is_vendor,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
        "roles": user.roles,
        "account_type": user.roles,
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    phone = request.phone.strip()
    email = str(request.email).strip().lower()
    existing = await db.execute(
        select(models.User.id).where(or_(models.User.phone == phone, models.User.email == email))
    )
    if existing.first():
        raise HTTPException(status_code=409, detail="An account with that phone or email already exists.")

    user = models.User(
        full_name=request.full_name.strip(),
        phone=phone,
        email=email,
        hashed_password=await run_in_threadpool(pwd_context.hash, request.password),
        roles=request.account_type,
        is_vendor=request.account_type == "seller",
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Could not create account. Check whether the phone or email is already registered.")

    access_token = create_access_token({"sub": user.id})
    return {"access_token": access_token, "token_type": "bearer", "user": _user_payload(user)}


@router.post("/login")
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    identity = request.phone.strip()
    result = await db.execute(
        select(models.User).where(or_(models.User.phone == identity, models.User.email == identity.lower()))
    )
    user = result.scalars().first()
    valid_password = user and await run_in_threadpool(verify_password, request.password, user.hashed_password)
    if not valid_password or user.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone/email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token({"sub": user.id})
    return {"access_token": access_token, "token_type": "bearer", "user": _user_payload(user)}


@router.get("/me")
async def current_user(user: models.User = Depends(get_current_user)):
    return _user_payload(user)


