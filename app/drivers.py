"""Delivery drivers: signup, admin approval and online/offline status.

The table is created with CREATE TABLE IF NOT EXISTS at startup, the same idea
as ensure_business_contact_columns() in db.py. Nothing here alters an existing
table, and permission to drive comes from delivery_drivers.status (the users.roles
column is deliberately left alone so existing role checks keep working).
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table,
    false, insert, select, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_admin, get_current_user
from app.db import engine, get_db

__all__ = ["router", "ensure_driver_tables", "drivers"]

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/drivers", tags=["Drivers"])

VEHICLE_TYPES = {"bike", "car", "van", "truck"}
STATUSES = {"pending", "verified", "rejected", "suspended"}


def _utcnow() -> datetime:
    """Naive UTC timestamp — matches the DateTime column type (no tz)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_md = MetaData()
# Stub so the foreign key to users.id can resolve. It is never created:
# create_all below is limited to the drivers table only.
Table("users", _md, Column("id", Integer, primary_key=True))

drivers = Table(
    "delivery_drivers", _md,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"),
           nullable=False, unique=True),
    Column("vehicle_type", String, nullable=False),
    Column("plate_number", String, nullable=True),
    Column("city", String, nullable=False),
    Column("phone", String, nullable=False),
    Column("id_photo_url", String, nullable=True),
    Column("status", String, nullable=False, server_default="pending", index=True),
    Column("is_online", Boolean, nullable=False, server_default=false()),
    Column("rejection_note", String, nullable=True),
    Column("created_at", DateTime, nullable=False, default=_utcnow),
    Column("reviewed_at", DateTime, nullable=True),
    Column("reviewed_by", Integer, nullable=True),
)


async def ensure_driver_tables():
    """Create the drivers table if it is missing. Never stops the app from booting."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: _md.create_all(sync_conn, tables=[drivers], checkfirst=True)
            )
    except Exception:
        log.exception("Could not create delivery_drivers; driver endpoints will fail until fixed")


class DriverApply(BaseModel):
    vehicle_type: str
    plate_number: str | None = Field(default=None, max_length=20)
    city: str = Field(min_length=2, max_length=60)
    phone: str = Field(min_length=6, max_length=25)
    id_photo_url: str | None = Field(default=None, max_length=300)


def _out(row, admin: bool = False) -> dict:
    d = dict(row._mapping)
    for key in ("created_at", "reviewed_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    if not admin:
        d.pop("reviewed_by", None)
    return d


async def _driver_for(db: AsyncSession, user_id: int):
    return (await db.execute(select(drivers).where(drivers.c.user_id == user_id))).first()


# ------------------------------------------------------------------ driver side

@router.post("/apply", status_code=201)
async def apply(
    body: DriverApply,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehicle = body.vehicle_type.strip().lower()
    if vehicle not in VEHICLE_TYPES:
        raise HTTPException(422, "vehicle_type must be one of: " + ", ".join(sorted(VEHICLE_TYPES)))
    plate = (body.plate_number or "").strip().upper() or None
    if vehicle != "bike" and not plate:
        raise HTTPException(422, "A plate number is required for cars, vans and trucks")

    values = dict(
        vehicle_type=vehicle, plate_number=plate, city=body.city.strip(),
        phone=body.phone.strip(), id_photo_url=body.id_photo_url,
    )
    existing = await _driver_for(db, user.id)
    if existing:
        # A rejected driver may fix the details and apply again. Anyone else cannot.
        if existing.status != "rejected":
            raise HTTPException(409, f"You already have a driver application ({existing.status})")
        await db.execute(
            update(drivers).where(drivers.c.id == existing.id).values(
                **values, status="pending", rejection_note=None,
                reviewed_at=None, reviewed_by=None, created_at=_utcnow(),
            )
        )
    else:
        try:
            await db.execute(insert(drivers).values(user_id=user.id, **values))
        except IntegrityError as exc:
            await db.rollback()
            log.warning("Driver apply IntegrityError for user %s: %s", user.id, exc)
            raise HTTPException(409, "You already have a driver application")
    await db.commit()
    return _out(await _driver_for(db, user.id))


@router.get("/me")
async def my_application(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _driver_for(db, user.id)
    if not row:
        raise HTTPException(404, "No driver application yet")
    return _out(row)


@router.post("/me/online")
async def set_online(
    online: bool = Query(...),
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _driver_for(db, user.id)
    if not
