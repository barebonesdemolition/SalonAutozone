"""Alerts: a list on the site for each person, plus a pop-up on their phone (web push).

Web push needs a key pair (VAPID). It is made on first start and kept in the database,
so there is nothing to set up. Phones subscribe from the Alerts page; every alert is
saved first, so nothing is lost if a phone has alerts switched off.

Sending never raises: an alert that can't be delivered must not break an order.
"""
import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import (Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, delete, func,
                        insert, select, update)
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_user
from app.db import AsyncSessionLocal, engine, get_db

log = logging.getLogger(__name__)
router = APIRouter(tags=["Alerts"])

_md = MetaData()
notifications = Table("notifications", _md,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("title", String, nullable=False),
    Column("body", String, nullable=True),
    Column("url", String, nullable=True),
    Column("kind", String, nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow, index=True),
    Column("read_at", DateTime, nullable=True),
)
push_subs = Table("push_subscriptions", _md,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("endpoint", String, nullable=False, unique=True),
    Column("p256dh", String, nullable=False),
    Column("auth", String, nullable=False),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
)
app_keys = Table("app_keys", _md,
    Column("name", String, primary_key=True),
    Column("value", Text, nullable=False),
)

_vapid = {}
_tasks = set()


async def ensure_notify_tables():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: _md.create_all(c, checkfirst=True))
    except Exception:
        log.exception("Could not create alert tables")


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


async def vapid_keys():
    """(private PEM, public key for the browser). Made once and stored."""
    if _vapid:
        return _vapid["private"], _vapid["public"]
    async with AsyncSessionLocal() as db:
        rows = {r.name: r.value for r in (await db.execute(select(app_keys))).all()}
        if "vapid_private" not in rows:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
            pub = _b64(key.public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint))
            try:
                await db.execute(insert(app_keys).values(name="vapid_private", value=pem))
                await db.execute(insert(app_keys).values(name="vapid_public", value=pub))
                await db.commit()
                rows = {"vapid_private": pem, "vapid_public": pub}
            except Exception:  # another worker made them first
                await db.rollback()
                rows = {r.name: r.value for r in (await db.execute(select(app_keys))).all()}
    _vapid.update(private=rows["vapid_private"], public=rows["vapid_public"])
    return _vapid["private"], _vapid["public"]


def _send_one(sub, payload, private_pem):
    """Blocking send of one push. Returns False when the phone has gone (unsubscribe it)."""
    from pywebpush import WebPushException, webpush
    from py_vapid import Vapid
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=payload, vapid_private_key=Vapid.from_pem(private_pem.encode()),
                vapid_claims={"sub": "mailto:alerts@salonautozone.com"}, ttl=86400, timeout=10)
        return True
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code in (404, 410):
            return False
        log.warning("Push failed (%s): %s", code, str(exc)[:200])
        return True
    except Exception:
        log.exception("Push failed")
        return True


async def _push(user_ids, payload):
    try:
        private, _ = await vapid_keys()
        async with AsyncSessionLocal() as db:
            subs = [dict(r._mapping) for r in (await db.execute(select(push_subs).where(push_subs.c.user_id.in_(user_ids)))).all()]
            gone = []
            for sub in subs:
                if not await asyncio.to_thread(_send_one, sub, payload, private):
                    gone.append(sub["id"])
            if gone:
                await db.execute(delete(push_subs).where(push_subs.c.id.in_(gone)))
                await db.commit()
    except Exception:
        log.exception("Could not send push alerts")


async def notify(user_ids, title, body=None, url=None, kind=None):
    """Save an alert for each person and pop it up on their phones. Never raises."""
    ids = sorted({int(u) for u in (user_ids if isinstance(user_ids, (list, tuple, set)) else [user_ids]) if u})
    if not ids:
        return
    try:
        async with AsyncSessionLocal() as db:
            for uid in ids:
                await db.execute(insert(notifications).values(user_id=uid, title=title[:120], body=(body or "")[:300] or None,
                                                              url=url, kind=kind))
            await db.commit()
            has_phone = (await db.execute(select(func.count(push_subs.c.id)).where(push_subs.c.user_id.in_(ids)))).scalar()
    except Exception:
        log.exception("Could not save alert %r", title)
        return
    if has_phone:
        payload = json.dumps({"title": title, "body": body or "", "url": url or "/alerts"})
        task = asyncio.create_task(_push(ids, payload))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def admin_ids():
    try:
        async with AsyncSessionLocal() as db:
            return [r[0] for r in (await db.execute(select(models.User.id).where(models.User.is_admin.is_(True)))).all()]
    except Exception:
        return []


async def notify_admins(title, body=None, url="/admin", kind=None):
    await notify(await admin_ids(), title, body, url, kind)


# ---------- API ----------

@router.get("/api/alerts")
async def my_alerts(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(notifications).where(notifications.c.user_id == user.id)
                             .order_by(notifications.c.id.desc()).limit(60))).all()
    unread = (await db.execute(select(func.count(notifications.c.id)).where(
        notifications.c.user_id == user.id, notifications.c.read_at.is_(None)))).scalar() or 0
    phones = (await db.execute(select(func.count(push_subs.c.id)).where(push_subs.c.user_id == user.id))).scalar() or 0
    return {"unread": unread, "phones": phones, "alerts": [
        {"id": r.id, "title": r.title, "body": r.body, "url": r.url, "kind": r.kind, "read": r.read_at is not None,
         "created_at": r.created_at.isoformat()} for r in rows]}


@router.get("/api/alerts/unread")
async def unread_count(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    n = (await db.execute(select(func.count(notifications.c.id)).where(
        notifications.c.user_id == user.id, notifications.c.read_at.is_(None)))).scalar() or 0
    return {"unread": n}


@router.post("/api/alerts/read")
async def mark_read(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(update(notifications).where(notifications.c.user_id == user.id, notifications.c.read_at.is_(None))
                     .values(read_at=datetime.utcnow()))
    await db.commit()
    return {"ok": True}


@router.get("/api/push/key")
async def push_key():
    _, public = await vapid_keys()
    return {"key": public}


class SubKeys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class SubIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=800)
    keys: SubKeys


@router.post("/api/push/subscribe")
async def subscribe(body: SubIn, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not body.endpoint.startswith("https://"):
        raise HTTPException(400, "Not a push address")
    have = (await db.execute(select(push_subs).where(push_subs.c.endpoint == body.endpoint))).first()
    if have:
        await db.execute(update(push_subs).where(push_subs.c.id == have.id)
                         .values(user_id=user.id, p256dh=body.keys.p256dh, auth=body.keys.auth))
    else:
        count = (await db.execute(select(func.count(push_subs.c.id)).where(push_subs.c.user_id == user.id))).scalar() or 0
        if count >= 10:  # keep the newest 10 phones/browsers per person
            oldest = (await db.execute(select(push_subs.c.id).where(push_subs.c.user_id == user.id)
                                       .order_by(push_subs.c.id).limit(count - 9))).scalars().all()
            await db.execute(delete(push_subs).where(push_subs.c.id.in_(oldest)))
        await db.execute(insert(push_subs).values(user_id=user.id, endpoint=body.endpoint,
                                                  p256dh=body.keys.p256dh, auth=body.keys.auth))
    await db.commit()
    await notify(user.id, "Alerts are on", "You'll get a message here when something happens with your orders.", "/alerts", "welcome")
    return {"ok": True}


class UnsubIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=800)


@router.post("/api/push/unsubscribe")
async def unsubscribe(body: UnsubIn, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(push_subs).where(push_subs.c.endpoint == body.endpoint, push_subs.c.user_id == user.id))
    await db.commit()
    return {"ok": True}
