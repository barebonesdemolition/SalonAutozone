"""
Login help: forgot password, reset links, change password, and the admin's
"Users" tools for sorting out anyone's login problems.

The site can't send emails or texts yet, so a forgotten password works like this:
the user asks for help, the request shows on the admin dashboard, and the admin
sends a one-time reset link on WhatsApp. Passwords are stored scrambled (bcrypt),
so nobody, admins included, can read them; the reset link replaces them.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import (Column, DateTime, Integer, MetaData, String, Table, and_, desc, func,
                        inspect, insert, or_, select, text, update)
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import create_access_token, get_current_user, pwd_context, verify_password
from app.db import engine, get_db

log = logging.getLogger(__name__)

RESET_HOURS = 24
LOCK_AFTER = 8            # wrong passwords in a row ...
LOCK_MINUTES = 15         # ... pause the account this long
ADMIN_ROLES = {"admin", "superadmin"}

_md = MetaData()

login_events = Table(
    "login_events", _md,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=True, index=True),
    Column("identity", String, nullable=True),     # what was typed: phone or email
    Column("outcome", String, nullable=False),     # ok, wrong_password, no_account, inactive, locked, ...
    Column("actor", String, nullable=True),        # for admin actions: "key" or "user:<id>"
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow, index=True),
)

password_resets = Table(
    "password_resets", _md,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("token_hash", String, nullable=False, unique=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("expires_at", DateTime, nullable=False),
    Column("used_at", DateTime, nullable=True),
    Column("created_by", String, nullable=True),
)

reset_requests = Table(
    "password_reset_requests", _md,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=True, index=True),
    Column("identity", String, nullable=False),
    Column("status", String, nullable=False, default="open", index=True),  # open, done
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("done_at", DateTime, nullable=True),
)

USER_COLUMNS = {
    "phone_key": "VARCHAR",
    "last_login_at": "TIMESTAMP",
    "failed_logins": "INTEGER DEFAULT 0",
    "last_failed_at": "TIMESTAMP",
    "token_version": "INTEGER DEFAULT 0",
}


def phone_key(phone: str | None) -> str | None:
    """Last 8 digits, so +232 76 123456, 076123456 and 76123456 are the same number."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-8:] if len(digits) >= 7 else None


async def ensure_account_tables():
    """Create the login-help tables and add the new user columns to older databases."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: _md.create_all(c, checkfirst=True))

        def user_columns(sync_conn):
            insp = inspect(sync_conn)
            if not insp.has_table("users"):
                return None
            return {c["name"] for c in insp.get_columns("users")}

        existing = await conn.run_sync(user_columns)
        if existing is None:
            return
        for name, kind in USER_COLUMNS.items():
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {kind}"))

        # Fill in phone_key for accounts created before it existed.
        rows = (await conn.execute(text("SELECT id, phone FROM users WHERE phone_key IS NULL"))).all()
        for user_id, phone in rows:
            key = phone_key(phone)
            if key:
                await conn.execute(text("UPDATE users SET phone_key = :k WHERE id = :i"), {"k": key, "i": user_id})


def _now() -> datetime:
    return datetime.utcnow()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _public_base(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _roles(user) -> set:
    return {r.strip().lower() for r in (getattr(user, "roles", "") or "").split(",") if r.strip()}


def _is_admin_user(user) -> bool:
    return bool(user.is_admin) or bool(_roles(user) & ADMIN_ROLES)


async def record(db: AsyncSession, outcome: str, user_id=None, identity=None, actor=None):
    await db.execute(insert(login_events).values(
        user_id=user_id, identity=(identity or "")[:120] or None, outcome=outcome, actor=actor, created_at=_now()))


async def find_users(db: AsyncSession, identity: str) -> list:
    """Accounts matching a phone (any format) or an email."""
    identity = (identity or "").strip()
    if not identity:
        return []
    if "@" in identity:
        cond = func.lower(models.User.email) == identity.lower()
    else:
        key = phone_key(identity)
        cond = models.User.phone == identity if key is None else or_(models.User.phone_key == key, models.User.phone == identity)
    return list((await db.execute(select(models.User).where(cond).order_by(models.User.id))).scalars().all())


def is_locked(user) -> bool:
    return bool((user.failed_logins or 0) >= LOCK_AFTER and user.last_failed_at
                and user.last_failed_at > _now() - timedelta(minutes=LOCK_MINUTES))


def token_for(user) -> str:
    return create_access_token({"sub": user.id, "ver": user.token_version or 0})


async def set_password(db: AsyncSession, user, new_password: str):
    user.hashed_password = await run_in_threadpool(pwd_context.hash, new_password)
    user.token_version = (user.token_version or 0) + 1   # signs out every other device
    user.failed_logins = 0
    user.last_failed_at = None


# ---------------------------------------------------------------- public

router = APIRouter(tags=["Login help"])


class ForgotIn(BaseModel):
    identity: str = Field(min_length=3, max_length=120)


class ResetIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=8, max_length=128)


class ChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/api/auth/forgot")
async def forgot_password(body: ForgotIn, db: AsyncSession = Depends(get_db)):
    """Ask the team for a reset link. The answer is the same whether or not the account exists."""
    from app.config import get_settings
    identity = body.identity.strip()
    users = await find_users(db, identity)
    user_id = users[0].id if len(users) == 1 else None
    recent = (await db.execute(select(reset_requests.c.id).where(
        reset_requests.c.identity == identity, reset_requests.c.status == "open",
        reset_requests.c.created_at > _now() - timedelta(minutes=10)))).first()
    if not recent:
        await db.execute(insert(reset_requests).values(
            user_id=user_id, identity=identity[:120], status="open", created_at=_now()))
        await record(db, "reset_requested", user_id=user_id, identity=identity)
        await db.commit()
    wa = re.sub(r"\D", "", get_settings().ADMIN_WHATSAPP or "") or None
    return {"ok": True, "whatsapp": wa}


@router.get("/api/auth/reset/{token}")
async def check_reset(token: str, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(password_resets).where(password_resets.c.token_hash == _hash_token(token)))).first()
    if not row or row.used_at or row.expires_at < _now():
        raise HTTPException(400, "This reset link has expired or was already used. Ask us for a new one.")
    user = (await db.execute(select(models.User).where(models.User.id == row.user_id))).scalars().first()
    if not user:
        raise HTTPException(400, "This reset link has expired or was already used. Ask us for a new one.")
    return {"ok": True, "first_name": (user.full_name or "").split(" ")[0], "expires_at": row.expires_at.isoformat()}


@router.post("/api/auth/reset")
async def reset_password(body: ResetIn, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(password_resets).where(password_resets.c.token_hash == _hash_token(body.token)))).first()
    if not row or row.used_at or row.expires_at < _now():
        raise HTTPException(400, "This reset link has expired or was already used. Ask us for a new one.")
    user = (await db.execute(select(models.User).where(models.User.id == row.user_id))).scalars().first()
    if not user:
        raise HTTPException(400, "This reset link has expired or was already used. Ask us for a new one.")
    if user.is_active is False:
        raise HTTPException(403, "This account is switched off. Message us on WhatsApp.")
    # Only one use, even if two requests race.
    used = await db.execute(update(password_resets).where(
        password_resets.c.id == row.id, password_resets.c.used_at.is_(None)).values(used_at=_now()))
    if used.rowcount != 1:
        raise HTTPException(400, "This reset link has expired or was already used. Ask us for a new one.")
    await set_password(db, user, body.password)
    user.last_login_at = _now()
    await db.execute(update(reset_requests).where(
        reset_requests.c.user_id == user.id, reset_requests.c.status == "open").values(status="done", done_at=_now()))
    await record(db, "password_reset", user_id=user.id)
    await db.commit()
    await db.refresh(user)
    from app.routers.auth import _user_payload
    return {"access_token": token_for(user), "token_type": "bearer", "user": _user_payload(user)}


@router.post("/api/auth/change-password")
async def change_password(body: ChangeIn, user: models.User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    if not await run_in_threadpool(verify_password, body.current_password, user.hashed_password):
        raise HTTPException(400, "Your current password isn't right.")
    await set_password(db, user, body.new_password)
    await record(db, "password_changed", user_id=user.id)
    await db.commit()
    await db.refresh(user)
    return {"ok": True, "access_token": token_for(user), "token_type": "bearer"}


# ---------------------------------------------------------------- admin

def _actor(admin) -> str:
    return f"user:{admin.id}" if getattr(admin, "id", None) else "key"


def _user_row(u, open_request=False) -> dict:
    return {
        "id": u.id, "full_name": u.full_name, "phone": u.phone, "email": u.email,
        "roles": u.roles, "is_admin": _is_admin_user(u), "is_active": u.is_active is not False,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "failed_logins": u.failed_logins or 0,
        "last_failed_at": u.last_failed_at.isoformat() if u.last_failed_at else None,
        "locked": is_locked(u),
        "reset_requested": open_request,
    }


def _event(e, names=None) -> dict:
    return {"id": e.id, "user_id": e.user_id, "user_name": (names or {}).get(e.user_id),
            "identity": e.identity, "outcome": e.outcome, "actor": e.actor,
            "at": e.created_at.isoformat() if e.created_at else None}


def admin_router():
    # Imported here so app.routers.admin can import this module without a cycle.
    from app.routers.admin import admin_actor

    r = APIRouter(prefix="/api/admin", tags=["Admin: users"])

    async def _user(db, user_id):
        u = (await db.execute(select(models.User).where(models.User.id == user_id))).scalars().first()
        if not u:
            raise HTTPException(404, "User not found")
        return u

    async def _open_requests(db, ids):
        if not ids:
            return set()
        rows = (await db.execute(select(reset_requests.c.user_id).where(
            reset_requests.c.user_id.in_(ids), reset_requests.c.status == "open"))).all()
        return {x for (x,) in rows}

    @r.get("/users")
    async def list_users(q: str = "", limit: int = 50, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
        limit = max(1, min(limit, 200))
        query = select(models.User)
        q = q.strip()
        if q:
            like = f"%{q.lower()}%"
            conds = [func.lower(models.User.full_name).like(like), func.lower(models.User.email).like(like),
                     models.User.phone.like(f"%{q}%")]
            key = phone_key(q)
            if key and len(re.sub(r"\D", "", q)) >= 7:
                conds.append(models.User.phone_key == key)
            if q.isdigit():
                conds.append(models.User.id == int(q))
            query = query.where(or_(*conds))
        rows = (await db.execute(query.order_by(desc(models.User.id)).limit(limit))).scalars().all()
        open_ids = await _open_requests(db, [u.id for u in rows])
        return {"users": [_user_row(u, u.id in open_ids) for u in rows]}

    @r.get("/users/{user_id}")
    async def user_detail(user_id: int, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
        u = await _user(db, user_id)
        events = (await db.execute(select(login_events).where(login_events.c.user_id == user_id)
                                   .order_by(desc(login_events.c.id)).limit(25))).all()
        open_ids = await _open_requests(db, [u.id])
        return {"user": _user_row(u, u.id in open_ids), "events": [_event(e) for e in events]}

    @r.post("/users/{user_id}/reset-link")
    async def make_reset_link(user_id: int, request: Request, admin=Depends(admin_actor),
                              db: AsyncSession = Depends(get_db)):
        u = await _user(db, user_id)
        token = secrets.token_urlsafe(32)
        expires = _now() + timedelta(hours=RESET_HOURS)
        # Only the newest link works.
        await db.execute(update(password_resets).where(
            password_resets.c.user_id == u.id, password_resets.c.used_at.is_(None)).values(used_at=_now()))
        await db.execute(insert(password_resets).values(
            user_id=u.id, token_hash=_hash_token(token), created_at=_now(), expires_at=expires, created_by=_actor(admin)))
        await record(db, "admin_reset_link", user_id=u.id, actor=_actor(admin))
        await db.commit()
        url = f"{_public_base(request)}/reset-password?token={token}"
        first = (u.full_name or "").split(" ")[0]
        message = (f"Hello {first}, this is SalonAutoZone. Tap this link to choose a new password: {url}\n"
                   f"It works once and expires in {RESET_HOURS} hours. If you didn't ask for this, ignore this message.")
        digits = re.sub(r"\D", "", u.phone or "")
        if len(digits) == 9 and digits.startswith("0"):
            digits = "232" + digits[1:]
        elif len(digits) == 8:
            digits = "232" + digits
        from urllib.parse import quote
        return {"url": url, "expires_at": expires.isoformat(), "message": message,
                "whatsapp_url": f"https://wa.me/{digits}?text={quote(message)}" if len(digits) >= 8 else None}

    @r.post("/users/{user_id}/unlock")
    async def unlock(user_id: int, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
        u = await _user(db, user_id)
        u.failed_logins = 0
        u.last_failed_at = None
        await record(db, "admin_unlock", user_id=u.id, actor=_actor(admin))
        await db.commit()
        return {"ok": True}

    @r.post("/users/{user_id}/sign-out")
    async def sign_out_everywhere(user_id: int, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
        u = await _user(db, user_id)
        u.token_version = (u.token_version or 0) + 1
        await record(db, "admin_sign_out", user_id=u.id, actor=_actor(admin))
        await db.commit()
        return {"ok": True}

    @r.post("/users/{user_id}/active")
    async def set_active(user_id: int, active: bool, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
        u = await _user(db, user_id)
        if not active and _is_admin_user(u):
            raise HTTPException(400, "Admin accounts can't be switched off here.")
        u.is_active = active
        if not active:
            u.token_version = (u.token_version or 0) + 1
        await record(db, "admin_enable" if active else "admin_disable", user_id=u.id, actor=_actor(admin))
        await db.commit()
        return {"ok": True, "is_active": active}

    @r.get("/login-problems")
    async def login_problems(admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
        since = _now() - timedelta(days=7)
        failed = (await db.execute(select(login_events).where(
            login_events.c.outcome.in_(["wrong_password", "no_account", "inactive", "locked"]),
            login_events.c.created_at > since).order_by(desc(login_events.c.id)).limit(60))).all()
        requests = (await db.execute(select(reset_requests).where(reset_requests.c.status == "open")
                                     .order_by(desc(reset_requests.c.id)).limit(60))).all()
        ids = {e.user_id for e in failed if e.user_id} | {q.user_id for q in requests if q.user_id}
        names = {}
        if ids:
            names = dict((await db.execute(select(models.User.id, models.User.full_name)
                                           .where(models.User.id.in_(ids)))).all())
        return {
            "requests": [{"id": q.id, "user_id": q.user_id, "user_name": names.get(q.user_id), "identity": q.identity,
                          "at": q.created_at.isoformat() if q.created_at else None} for q in requests],
            "failed": [_event(e, names) for e in failed],
        }

    @r.post("/reset-requests/{request_id}/done")
    async def close_request(request_id: int, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
        res = await db.execute(update(reset_requests).where(reset_requests.c.id == request_id)
                               .values(status="done", done_at=_now()))
        if res.rowcount != 1:
            raise HTTPException(404, "Request not found")
        await db.commit()
        return {"ok": True}

    return r
