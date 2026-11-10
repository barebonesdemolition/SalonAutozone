"""Delivery drivers: signup, admin approval and online/offline status.

The table is created with CREATE TABLE IF NOT EXISTS at startup, the same idea
as ensure_business_contact_columns() in db.py. Nothing here alters an existing
table, and permission to drive comes from delivery_drivers.status (the users.roles
column is deliberately left alone so existing role checks keep working).
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table,
    false, func, insert, inspect, select, text, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_user
from app.routers.admin import admin_actor
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
    # Read from the photos at sign-up (the rider can correct them); only admins see these.
    Column("plate_photo_url", String, nullable=True),
    Column("id_type", String, nullable=True),
    Column("id_name", String, nullable=True),
    Column("id_number", String, nullable=True, index=True),
    Column("id_expiry", String, nullable=True),
    Column("status", String, nullable=False, server_default="pending", index=True),
    Column("is_online", Boolean, nullable=False, server_default=false()),
    Column("rejection_note", String, nullable=True),
    Column("created_at", DateTime, nullable=False, default=_utcnow),
    Column("reviewed_at", DateTime, nullable=True),
    Column("reviewed_by", Integer, nullable=True),
)


driver_scans = Table(
    "driver_scans", _md,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("kind", String, nullable=False),
    Column("created_at", DateTime, nullable=False, default=_utcnow, index=True),
)

NEW_COLUMNS = {"plate_photo_url": "VARCHAR", "id_type": "VARCHAR", "id_name": "VARCHAR",
               "id_number": "VARCHAR", "id_expiry": "VARCHAR"}


async def ensure_driver_tables():
    """Create the drivers tables and add newer columns. Never stops the app from booting."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: _md.create_all(sync_conn, tables=[drivers, driver_scans], checkfirst=True)
            )
            have = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("delivery_drivers")})
            for name, kind in NEW_COLUMNS.items():
                if name not in have:
                    await conn.execute(text(f"ALTER TABLE delivery_drivers ADD COLUMN {name} {kind}"))
    except Exception:
        log.exception("Could not create delivery_drivers; driver endpoints will fail until fixed")


ID_TYPES = {"national_id": "National ID card", "drivers_licence": "Driver's licence", "voter_card": "Voter ID card",
            "passport": "Passport", "ecowas_id": "ECOWAS ID card", "other": "Other ID"}


def clean_plate(v: str | None) -> str | None:
    v = re.sub(r"[^A-Z0-9 -]", "", (v or "").upper())
    v = re.sub(r"\s+", " ", v).strip(" -")
    return v[:16] or None


def clean_id_number(v: str | None) -> str | None:
    v = re.sub(r"[^A-Z0-9/ -]", "", (v or "").upper())
    v = re.sub(r"\s+", " ", v).strip(" -/")
    return v[:30] or None


def _photo_url(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if v.startswith("https://") or (v.startswith("/") and not v.startswith("//")):
        return v[:300]
    raise HTTPException(422, "Photo link must start with https://")


class DriverApply(BaseModel):
    vehicle_type: str
    plate_number: str | None = Field(default=None, max_length=20)
    city: str = Field(min_length=2, max_length=60)
    phone: str = Field(min_length=6, max_length=25)
    id_photo_url: str | None = Field(default=None, max_length=300)
    plate_photo_url: str | None = Field(default=None, max_length=300)
    id_type: str | None = None
    id_name: str | None = Field(default=None, max_length=100)
    id_number: str | None = Field(default=None, max_length=40)
    id_expiry: str | None = Field(default=None, max_length=10)


def names_match(account: str | None, on_id: str | None) -> bool | None:
    """True when at least two name words (or the only one) match, ignoring order and case."""
    a = {w for w in re.findall(r"[a-z]+", (account or "").lower()) if len(w) > 1}
    b = {w for w in re.findall(r"[a-z]+", (on_id or "").lower()) if len(w) > 1}
    if not a or not b:
        return None
    return len(a & b) >= min(2, len(a), len(b))


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
    plate = clean_plate(body.plate_number)
    if vehicle != "bike" and not plate:
        raise HTTPException(422, "A plate number is required for cars, vans and trucks")
    id_type = (body.id_type or "").strip().lower() or None
    if id_type and id_type not in ID_TYPES:
        raise HTTPException(422, "Choose an ID type from the list")
    expiry = (body.id_expiry or "").strip() or None
    if expiry and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry):
        raise HTTPException(422, "ID expiry date must look like 2030-12-31")
    if expiry and expiry < _utcnow().date().isoformat():
        raise HTTPException(422, "This ID has expired. Please use one that is still valid.")

    values = dict(
        vehicle_type=vehicle, plate_number=plate, city=body.city.strip(),
        phone=body.phone.strip(), id_photo_url=_photo_url(body.id_photo_url),
        plate_photo_url=_photo_url(body.plate_photo_url), id_type=id_type,
        id_name=(body.id_name or "").strip() or None, id_number=clean_id_number(body.id_number), id_expiry=expiry,
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
    if not row or row.status != "verified":
        raise HTTPException(403, "Only verified drivers can go online")
    await db.execute(update(drivers).where(drivers.c.id == row.id).values(is_online=online))
    await db.commit()
    return {"is_online": online}


# ------------------------------------------------------------------ photo scanning

SCANS_PER_DAY = 20

ID_PROMPT = """This photo should show an identity document, most likely from Sierra Leone:
a national ID card, driver's licence, voter ID card, passport or ECOWAS ID card.
Return JSON only:
{"document_type": "national_id" | "drivers_licence" | "voter_card" | "passport" | "ecowas_id" | "other" | "not_an_id",
 "full_name": "name exactly as printed, else null",
 "id_number": "the document's ID / licence / NIN number exactly as printed, else null",
 "date_of_birth": "YYYY-MM-DD or null",
 "expiry_date": "YYYY-MM-DD or null",
 "readable": true or false,
 "problem": "short reason if hard to read (blurry, glare, cut off, too dark), else null"}
Never guess a character you can't read: use null for that field instead."""

PLATE_PROMPT = """This photo should show a vehicle number plate in Sierra Leone (motorbike/okada, keke, car or van).
Return JSON only:
{"plate_number": "exactly as printed, uppercase, else null",
 "vehicle": "motorbike" | "tricycle" | "car" | "van" | "truck" | "unknown",
 "colour": "main colour of the vehicle if visible, else null",
 "make": "make if visible (e.g. Haojue, TVS, Bajaj, Toyota), else null",
 "readable": true or false,
 "problem": "short reason if hard to read, else null"}
Never guess a character you can't read: use null instead."""


def _ask_ai(image: bytes, mime: str, prompt: str) -> dict:
    from app.vision import ask_json
    return ask_json(image, mime, prompt)


def _txt(v, n):
    v = re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""
    return None if not v or v.lower() in ("null", "none", "unknown", "n/a") else v[:n]


def _date(v):
    v = _txt(v, 10)
    return v if v and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None


def clean_id_scan(raw: dict) -> dict:
    doc = (_txt(raw.get("document_type"), 20) or "other").lower()
    if doc == "not_an_id":
        return {"ok": False, "problem": "That doesn't look like an ID card. Take a photo of the front of your ID."}
    out = {"ok": bool(raw.get("readable", True)), "id_type": doc if doc in ID_TYPES else "other",
           "full_name": _txt(raw.get("full_name"), 100), "id_number": clean_id_number(_txt(raw.get("id_number"), 40)),
           "date_of_birth": _date(raw.get("date_of_birth")), "expiry_date": _date(raw.get("expiry_date")),
           "problem": _txt(raw.get("problem"), 120)}
    if not out["id_number"] and not out["full_name"]:
        out["ok"] = False
        out["problem"] = out["problem"] or "We couldn't read the card. Try again in good light, with the whole card in the photo."
    if out["expiry_date"] and out["expiry_date"] < _utcnow().date().isoformat():
        out["expired"] = True
    return out


def clean_plate_scan(raw: dict) -> dict:
    vehicle = (_txt(raw.get("vehicle"), 12) or "unknown").lower()
    out = {"ok": bool(raw.get("readable", True)), "plate_number": clean_plate(_txt(raw.get("plate_number"), 20)),
           "vehicle": vehicle if vehicle in ("motorbike", "tricycle", "car", "van", "truck") else "unknown",
           "colour": _txt(raw.get("colour"), 30), "make": _txt(raw.get("make"), 40), "problem": _txt(raw.get("problem"), 120)}
    out["vehicle_type"] = {"motorbike": "bike", "tricycle": "bike", "car": "car", "van": "van", "truck": "truck"}.get(out["vehicle"])
    if not out["plate_number"]:
        out["ok"] = False
        out["problem"] = out["problem"] or "We couldn't read the plate. Stand closer, straight in front of it, in good light."
    return out


@router.post("/scan")
async def scan_document(
    kind: str = Query(..., pattern="^(id|plate)$"),
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read an ID card or a number plate from a photo, to fill in the rider sign-up form."""
    from app.vision import vision_enabled
    if not vision_enabled():
        raise HTTPException(503, "Photo reading isn't switched on. Please type the details instead.")
    since = _utcnow() - timedelta(days=1)
    used = (await db.execute(select(func.count(driver_scans.c.id)).where(
        driver_scans.c.user_id == user.id, driver_scans.c.created_at > since))).scalar() or 0
    if used >= SCANS_PER_DAY:
        raise HTTPException(429, "You've scanned a lot of photos today. Please type the details instead.")
    mime = (file.content_type or "").lower()
    if mime not in ("image/jpeg", "image/jpg", "image/png", "image/webp"):
        raise HTTPException(400, "Use a JPG, PNG or WEBP photo.")
    image = await file.read()
    if len(image) > 8 * 1024 * 1024:
        raise HTTPException(400, "That photo is too big. Try again.")
    try:
        raw = await run_in_threadpool(_ask_ai, image, "image/jpeg" if mime == "image/jpg" else mime,
                                      ID_PROMPT if kind == "id" else PLATE_PROMPT)
    except ValueError:
        raise HTTPException(502, "We couldn't read the result. Try the photo again.")
    except Exception:
        log.exception("Driver %s scan failed", kind)
        raise HTTPException(502, "Photo reading isn't working right now. Please type the details instead.")
    await db.execute(insert(driver_scans).values(user_id=user.id, kind=kind, created_at=_utcnow()))
    await db.commit()
    return clean_id_scan(raw) if kind == "id" else clean_plate_scan(raw)


# ------------------------------------------------------------------ admin side

@router.get("/admin/list")
async def admin_list(
    status: str = Query("pending"),
    admin=Depends(admin_actor),
    db: AsyncSession = Depends(get_db),
):
    status = status.lower()
    if status != "all" and status not in STATUSES:
        raise HTTPException(422, "status must be all, " + ", ".join(sorted(STATUSES)))
    u = models.User.__table__
    query = (
        select(drivers, u.c.full_name, u.c.email)
        .select_from(drivers.join(u, u.c.id == drivers.c.user_id))
        .order_by(drivers.c.created_at.desc())
        .limit(200)
    )
    if status != "all":
        query = query.where(drivers.c.status == status)
    rows = (await db.execute(query)).all()
    out = [_out(r, admin=True) for r in rows]
    # Flag IDs or plates that another rider has also used.
    ids = {d["id_number"] for d in out if d.get("id_number")}
    plates = {d["plate_number"] for d in out if d.get("plate_number")}
    used = []
    if ids or plates:
        conds = []
        if ids:
            conds.append(drivers.c.id_number.in_(ids))
        if plates:
            conds.append(drivers.c.plate_number.in_(plates))
        from sqlalchemy import or_
        used = (await db.execute(select(drivers.c.id, drivers.c.id_number, drivers.c.plate_number).where(or_(*conds)))).all()
    for d in out:
        d["id_type_label"] = ID_TYPES.get(d.get("id_type") or "", None)
        d["name_matches"] = names_match(d.get("full_name"), d.get("id_name"))
        d["same_id_as"] = [r.id for r in used if r.id != d["id"] and d.get("id_number") and r.id_number == d["id_number"]]
        d["same_plate_as"] = [r.id for r in used if r.id != d["id"] and d.get("plate_number") and r.plate_number == d["plate_number"]]
    return {"count": len(rows), "drivers": out}


@router.post("/admin/{driver_id}/status")
async def admin_set_status(
    driver_id: int,
    status: str = Query(...),
    note: str | None = Query(None, max_length=300),
    admin=Depends(admin_actor),
    db: AsyncSession = Depends(get_db),
):
    status = status.lower()
    if status not in STATUSES:
        raise HTTPException(422, "status must be one of: " + ", ".join(sorted(STATUSES)))
    row = (await db.execute(select(drivers).where(drivers.c.id == driver_id))).first()
    if not row:
        raise HTTPException(404, "Driver not found")
    values = dict(
        status=status, reviewed_at=_utcnow(), reviewed_by=admin.id,
        rejection_note=note if status in ("rejected", "suspended") else None,
    )
    if status != "verified":
        values["is_online"] = False  # a driver who is not verified can never stay online
    await db.execute(update(drivers).where(drivers.c.id == driver_id).values(**values))
    await db.commit()
    return {"id": driver_id, "status": status}   
