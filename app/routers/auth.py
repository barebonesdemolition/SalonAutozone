# app/auth.py
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


# ---------- Environment Configuration ----------

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY environment variable is required. "
        "Set it in Render → Environment before deploying. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
ISSUER = "salon-car-parts"
AUDIENCE = "salon-car-parts"

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
)

# NOTE: tokenUrl must match the actual login route.
# - If you mount the auth router at root:  "/api/auth/login"
# - If you prefix with /api/v1:            "/api/v1/auth/login"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ADMIN_ROLES = {"admin", "superadmin"}


# ---------- Password Hashing ----------

def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt. CPU-bound; call via run_in_threadpool in async paths."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash. CPU-bound; call via run_in_threadpool in async paths."""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        # Malformed hash in the DB — treat as a failed login rather than a 500.
        return False


# ---------- JWT Token Management ----------

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token with standard claims."""
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
    # Ensure `sub` is a string per RFC 7519.
    if "sub" in to_encode and not isinstance(to_encode["sub"], str):
        to_encode["sub"] = str(to_encode["sub"])
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on any failure."""
    return jwt.decode(
        token,
        SECRET_KEY,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
    )


# ---------- FastAPI Dependencies ----------

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> models.User:
    """Resolve the authenticated user from the Bearer token."""
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
    """Like get_current_user, but also rejects disabled accounts."""
    if getattr(user, "is_active", True) is False:
        raise HTTPException(status_code=403, detail="Inactive user")
    return user


# ---------- Role Helpers ----------

def _parse_roles(user: models.User) -> set[str]:
    """Parse the comma-separated `roles` column into a lowercase set."""
    raw = getattr(user, "roles", None) or ""
    return {r.strip().lower() for r in raw.split(",") if r.strip()}


def user_has_role(user: models.User, *roles: str) -> bool:
    """True if the user has any of the given roles."""
    wanted = {r.lower() for r in roles}
    return bool(_parse_roles(user) & wanted)


async def get_current_admin(
    user: models.User = Depends(get_current_user),
) -> models.User:
    """Dependency that requires the authenticated user to be an admin."""
    if not (_parse_roles(user) & ADMIN_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user


def require_role(*roles: str):
    """
    Factory for role-gated endpoints.

    Usage:
        @router.post("/listings", dependencies=[Depends(require_role("seller", "vendor"))])
        async def create_listing(...): ...

        # or, if you need the user object in the handler:
        async def create_listing(user: models.User = Depends(require_role("seller", "vendor"))): ...
    """
    wanted = {r.lower() for r in roles}

    async def _checker(user: models.User = Depends(get_current_user)) -> models.User:
        if not (_parse_roles(user) & wanted):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _checker
