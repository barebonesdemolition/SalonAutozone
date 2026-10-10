from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db import get_db
from app import models

from app.db import DATABASE_URL

SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("JWT_SECRET_KEY") or ""
if not SECRET_KEY:
    if DATABASE_URL.startswith("sqlite"):
        # Local development only. Never used against a real (Postgres) database.
        SECRET_KEY = "dev-only-secret-not-for-production"
    else:
        raise RuntimeError(
            "SECRET_KEY is not set. Set it to a long random value in your hosting environment "
            "(e.g. Render > Environment). Without it, anyone could forge login tokens."
        )
if len(SECRET_KEY) < 32 and not DATABASE_URL.startswith("sqlite"):
    raise RuntimeError("SECRET_KEY is too short. Use at least 32 random characters.")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
ISSUER = "salon-car-parts"
AUDIENCE = "salon-car-parts"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ADMIN_ROLES = {"admin", "superadmin"}


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    to_encode = data.copy()
    to_encode.update({
        "exp": now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)),
        "iat": now,
        "nbf": now,
        "jti": str(uuid.uuid4()),
        "iss": ISSUER,
        "aud": AUDIENCE,
        "type": "access",
    })
    if "sub" in to_encode and not isinstance(to_encode["sub"], str):
        to_encode["sub"] = str(to_encode["sub"])
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token, SECRET_KEY, algorithms=[ALGORITHM],
        audience=AUDIENCE, issuer=ISSUER,
    )


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        if payload.get("type") != "access":
            raise credentials_exception
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        try:
            user_pk = int(sub)
        except (TypeError, ValueError):
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(models.User).where(models.User.id == user_pk))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(
    user: models.User = Depends(get_current_user),
) -> models.User:
    if getattr(user, "is_active", True) is False:
        raise HTTPException(status_code=403, detail="Inactive user")
    return user


def _parse_roles(user: models.User) -> set:
    raw = getattr(user, "roles", None) or ""
    return {r.strip().lower() for r in raw.split(",") if r.strip()}


def user_has_role(user: models.User, *roles: str) -> bool:
    wanted = {r.lower() for r in roles}
    return bool(_parse_roles(user) & wanted)


async def get_current_admin(
    user: models.User = Depends(get_current_user),
) -> models.User:
    if not (_parse_roles(user) & ADMIN_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user


def require_role(*roles: str):
    wanted = {r.lower() for r in roles}

    async def _checker(user: models.User = Depends(get_current_user)) -> models.User:
        if not (_parse_roles(user) & wanted):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _checker
