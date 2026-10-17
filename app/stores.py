"""
Business pages and stock tools.

- Owners personalise their page: cover photo, colour, tagline, opening hours,
  address, call number, social links, the car makes they specialise in.
- Stock scanner: the owner photographs a shelf or a part with their phone; the AI
  lists each part it sees (name, category, brand, part number, fitting cars) and
  where it is in the photo, so the page can cut out a photo for each listing.
  The owner checks the list, adds prices and publishes everything in one go.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (Column, DateTime, Integer, MetaData, Table, func, inspect, insert, select, text)
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_user
from app.config import get_settings
from app.db import engine, get_db

log = logging.getLogger(__name__)

ACCENTS = {
    "teal": "#0e7c7b", "blue": "#1d5fbf", "red": "#c0352b", "orange": "#d9661f",
    "green": "#1f8a4c", "purple": "#6b3fa0", "gold": "#b8860b", "black": "#1c2b2b",
}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CATEGORIES = ["Fluids", "Filters", "Batteries", "Brakes", "Engine", "Suspension", "Electrical",
              "Tires & Wheels", "Transmission", "Body", "Interior", "Exhaust", "Lighting", "Audio", "Accessories"]
CONDITIONS = {"new", "used", "refurbished"}
SCANS_PER_DAY = 60
MAX_ITEMS_PER_SCAN = 30
MAX_BULK = 50

PROFILE_COLUMNS = {
    "tagline": "VARCHAR", "cover_url": "VARCHAR", "accent": "VARCHAR", "address": "VARCHAR",
    "hours": "TEXT", "call_phone": "VARCHAR", "facebook": "VARCHAR", "instagram": "VARCHAR",
    "tiktok": "VARCHAR", "specialties": "VARCHAR",
}

_md = MetaData()
stock_scans = Table(
    "stock_scans", _md,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("items_found", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow, index=True),
)


async def ensure_store_tables():
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: _md.create_all(c, checkfirst=True))

        def cols(sync_conn):
            insp = inspect(sync_conn)
            return {c["name"] for c in insp.get_columns("businesses")} if insp.has_table("businesses") else None

        existing = await conn.run_sync(cols)
        if existing is None:
            return
        for name, kind in PROFILE_COLUMNS.items():
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE businesses ADD COLUMN {name} {kind}"))


def parse_hours(raw) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    return {d: data[d] for d in DAYS if isinstance(data, dict) and d in data}


def profile_fields(b) -> dict:
    """Personalisation fields for the public business data."""
    accent = getattr(b, "accent", None)
    return {
        "tagline": getattr(b, "tagline", None),
        "cover_url": getattr(b, "cover_url", None),
        "accent": accent if accent in ACCENTS else None,
        "accent_hex": ACCENTS.get(accent or "", None),
        "address": getattr(b, "address", None),
        "hours": parse_hours(getattr(b, "hours", None)),
        "call_phone": getattr(b, "call_phone", None),
        "facebook": getattr(b, "facebook", None),
        "instagram": getattr(b, "instagram", None),
        "tiktok": getattr(b, "tiktok", None),
        "specialties": [s for s in (getattr(b, "specialties", None) or "").split(",") if s],
    }


def _safe_url(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if v.startswith("https://") or (v.startswith("/") and not v.startswith("//")):
        return v[:500]
    raise ValueError("Use a link that starts with https://")


def _handle(v: str | None, site: str) -> str | None:
    """Accept '@name', 'name' or a full link; store a full https link."""
    v = (v or "").strip()
    if not v:
        return None
    if v.startswith("https://"):
        host = re.sub(r"^https://(www\.)?", "", v).split("/")[0].lower()
        if not host.endswith(site):
            raise ValueError(f"That isn't a {site} link")
        return v[:300]
    name = v.lstrip("@").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", name):
        raise ValueError("Enter the page name, like @yourshop")
    return f"https://www.{site}/{'@' if site == 'tiktok.com' else ''}{name}"


class ProfileIn(BaseModel):
    tagline: str | None = Field(default=None, max_length=90)
    cover_url: str | None = None
    accent: str | None = None
    address: str | None = Field(default=None, max_length=160)
    hours: dict | None = None
    call_phone: str | None = Field(default=None, max_length=30)
    facebook: str | None = None
    instagram: str | None = None
    tiktok: str | None = None
    specialties: list[str] | None = None

    @field_validator("cover_url")
    @classmethod
    def _cover(cls, v):
        return _safe_url(v)

    @field_validator("accent")
    @classmethod
    def _accent(cls, v):
        if v and v not in ACCENTS:
            raise ValueError("Choose one of the colours")
        return v or None

    @field_validator("hours")
    @classmethod
    def _hours(cls, v):
        if not v:
            return None
        out = {}
        for day, val in v.items():
            if day not in DAYS:
                raise ValueError("Unknown day")
            val = (val or "").strip().lower()
            if val in ("", "closed"):
                out[day] = "closed"
                continue
            m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)", val)
            if not m:
                raise ValueError("Hours must look like 08:00-18:00")
            out[day] = val
        return out

    @field_validator("call_phone")
    @classmethod
    def _phone(cls, v):
        v = (v or "").strip()
        if not v:
            return None
        if len(re.sub(r"\D", "", v)) < 8:
            raise ValueError("Enter a full phone number")
        return v

    @field_validator("facebook")
    @classmethod
    def _fb(cls, v):
        return _handle(v, "facebook.com")

    @field_validator("instagram")
    @classmethod
    def _ig(cls, v):
        return _handle(v, "instagram.com")

    @field_validator("tiktok")
    @classmethod
    def _tt(cls, v):
        return _handle(v, "tiktok.com")

    @field_validator("specialties")
    @classmethod
    def _spec(cls, v):
        if not v:
            return None
        clean = []
        for s in v[:12]:
            s = re.sub(r"[^A-Za-z0-9 .-]", "", str(s)).strip()[:30]
            if s and s not in clean:
                clean.append(s)
        return clean or None

    @field_validator("tagline", "address")
    @classmethod
    def _strip(cls, v):
        return (v or "").strip() or None


async def _my_business(db: AsyncSession, user) -> models.Business:
    biz = (await db.execute(select(models.Business).where(models.Business.owner_id == user.id))).scalars().first()
    if not biz:
        raise HTTPException(404, "Apply as a business first.")
    return biz


router = APIRouter(tags=["Business pages and stock"])


@router.put("/api/businesses/mine/profile")
async def update_profile(body: ProfileIn, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Personalise the business page. Doesn't send the business back for checking."""
    biz = await _my_business(db, user)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key == "hours":
            value = json.dumps(value) if value else None
        elif key == "specialties":
            value = ",".join(value) if value else None
        setattr(biz, key, value)
    await db.commit()
    await db.refresh(biz)
    return {"ok": True, **profile_fields(biz)}


@router.get("/api/stores/options")
async def store_options():
    return {"accents": ACCENTS, "days": DAYS, "categories": CATEGORIES}


# ---------------------------------------------------------------- stock scanner

PROMPT = """You help car parts shops in Sierra Leone list their stock online.
Look at this photo from a shop. List every separate car part or product you can see
(boxed or loose). Count identical items as one entry with a quantity.

Return JSON only, in this exact shape:
{"items": [{
  "name": "short shop-style name, e.g. 'Front brake pads' or 'Oil filter'",
  "category": one of %(cats)s,
  "brand": "brand printed on the box or part, else null",
  "part_number": "part/OEM number if you can read it clearly, else null",
  "compatible_make": "car make it is for if printed or obvious, else null",
  "compatible_model": "car model if printed or obvious, else null",
  "condition": "new" if boxed or clearly unused, else "used",
  "quantity": number of this item visible,
  "notes": "one short sentence a buyer would find useful, else null",
  "confidence": "high" | "medium" | "low",
  "box_2d": [ymin, xmin, ymax, xmax] around one example of the item, scaled 0-1000
}]}

Rules: never guess a part number you can't read; use null instead. If the photo has no car
parts, return {"items": []}. At most %(max)d items."""


def _clean_text(v, limit):
    if v is None:
        return None
    v = re.sub(r"\s+", " ", str(v)).strip()
    if not v or v.lower() in ("null", "none", "unknown", "n/a"):
        return None
    return v[:limit]


def clean_items(raw) -> list[dict]:
    """Keep only well-formed items from the AI's answer."""
    items = raw.get("items") if isinstance(raw, dict) else raw
    out = []
    for it in (items or [])[:MAX_ITEMS_PER_SCAN]:
        if not isinstance(it, dict):
            continue
        name = _clean_text(it.get("name"), 120)
        if not name:
            continue
        cat = _clean_text(it.get("category"), 40)
        cat = next((c for c in CATEGORIES if cat and c.lower() == cat.lower()), "Accessories")
        cond = (_clean_text(it.get("condition"), 20) or "used").lower()
        try:
            qty = max(1, min(999, int(it.get("quantity") or 1)))
        except (TypeError, ValueError):
            qty = 1
        box = it.get("box_2d")
        if (isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(n, (int, float)) for n in box)):
            y0, x0, y1, x1 = (max(0, min(1000, int(n))) for n in box)
            box = [y0, x0, y1, x1] if y1 - y0 >= 20 and x1 - x0 >= 20 else None
        else:
            box = None
        conf = (_clean_text(it.get("confidence"), 10) or "medium").lower()
        out.append({
            "name": name, "category": cat,
            "brand": _clean_text(it.get("brand"), 60),
            "part_number": _clean_text(it.get("part_number"), 60),
            "compatible_make": _clean_text(it.get("compatible_make"), 40),
            "compatible_model": _clean_text(it.get("compatible_model"), 60),
            "condition": cond if cond in CONDITIONS else "used",
            "quantity": qty,
            "notes": _clean_text(it.get("notes"), 200),
            "confidence": conf if conf in ("high", "medium", "low") else "medium",
            "box": box,
        })
    return out


def _ask_ai(image: bytes, mime: str) -> dict:
    from google import genai
    from google.genai import types
    cfg = get_settings()
    client = genai.Client(api_key=cfg.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=cfg.GEMINI_MODEL,
        contents=[types.Part.from_bytes(data=image, mime_type=mime),
                  PROMPT % {"cats": json.dumps(CATEGORIES), "max": MAX_ITEMS_PER_SCAN}],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
    )
    txt = (response.text or "").strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
    return json.loads(txt)


@router.post("/api/stock/scan")
async def scan_stock(file: UploadFile = File(...), user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _my_business(db, user)
    if not get_settings().GEMINI_API_KEY:
        raise HTTPException(503, "The stock scanner isn't switched on yet. Add parts by hand for now.")
    since = datetime.utcnow() - timedelta(days=1)
    used = (await db.execute(select(func.count(stock_scans.c.id)).where(
        stock_scans.c.user_id == user.id, stock_scans.c.created_at > since))).scalar() or 0
    if used >= SCANS_PER_DAY:
        raise HTTPException(429, f"You've scanned {SCANS_PER_DAY} photos today. Try again tomorrow, or add parts by hand.")
    mime = (file.content_type or "").lower()
    if mime not in ("image/jpeg", "image/jpg", "image/png", "image/webp"):
        raise HTTPException(400, "Use a JPG, PNG or WEBP photo.")
    image = await file.read()
    if len(image) > 8 * 1024 * 1024:
        raise HTTPException(400, "That photo is too big. The page normally shrinks photos; try again.")
    try:
        raw = await run_in_threadpool(_ask_ai, image, "image/jpeg" if mime == "image/jpg" else mime)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(502, "We couldn't read the result. Try the photo again.")
    except Exception:
        log.exception("Stock scan failed")
        raise HTTPException(502, "The scanner isn't working right now. Try again in a minute.")
    items = clean_items(raw)
    await db.execute(insert(stock_scans).values(user_id=user.id, items_found=len(items), created_at=datetime.utcnow()))
    await db.commit()
    return {"items": items, "scans_left_today": max(0, SCANS_PER_DAY - used - 1)}


class StockItem(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str
    price_sll: float = Field(gt=0, le=10_000_000_000)
    stock_quantity: int = Field(default=1, ge=1, le=9999)
    condition: str = "new"
    brand: str | None = Field(default=None, max_length=60)
    part_number: str | None = Field(default=None, max_length=60)
    compatible_make: str | None = Field(default=None, max_length=40)
    compatible_model: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v not in CATEGORIES:
            raise ValueError("Choose a category from the list")
        return v

    @field_validator("condition")
    @classmethod
    def _cond(cls, v):
        v = (v or "").lower()
        if v not in CONDITIONS:
            raise ValueError("Condition must be new, used or refurbished")
        return v


class BulkIn(BaseModel):
    items: list[StockItem] = Field(min_length=1, max_length=MAX_BULK)


@router.post("/api/stock/bulk", status_code=201)
async def add_stock(body: BulkIn, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    biz = await _my_business(db, user)
    created = []
    for it in body.items:
        name = it.name.strip()
        if it.brand and it.brand.lower() not in name.lower():
            name = f"{it.brand.strip()} {name}"[:120]
        extra = []
        if it.part_number:
            extra.append(f"Part number: {it.part_number.strip()}")
        desc = "\n".join(x for x in [(it.description or "").strip(), *extra] if x) or None
        part = models.PartListing(
            name=name, category=it.category, price_sll=it.price_sll, stock_quantity=it.stock_quantity,
            condition=it.condition, compatible_make=(it.compatible_make or "").strip() or None,
            compatible_model=(it.compatible_model or "").strip() or None, description=desc,
            location=biz.city, contact_phone=getattr(biz, "call_phone", None) or (f"+{biz.whatsapp}" if biz.whatsapp else None),
            seller_id=user.id, vendor_id=user.id,
        )
        db.add(part)
        created.append(part)
    await db.commit()
    return {"created": [{"id": p.id, "name": p.name} for p in created], "store": biz.slug}
