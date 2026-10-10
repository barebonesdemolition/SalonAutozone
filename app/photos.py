"""
Listing photo galleries (several photos per part or car).

The listing's own image_url stays the cover photo, so every page that only
knows about one photo keeps working. Extra photos live in listing_images.
The table is created at start-up with CREATE TABLE IF NOT EXISTS, like the
delivery tables.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_user
from app.db import engine, get_db

MAX_PHOTOS = 8
LISTING_TYPES = {"part": models.PartListing, "vehicle": models.VehicleListing}

_md = MetaData()
images = Table(
    "listing_images", _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("listing_type", String(10), nullable=False, index=True),
    Column("listing_id", Integer, nullable=False, index=True),
    Column("url", String, nullable=False),
    Column("position", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
)

router = APIRouter(prefix="/api/photos", tags=["Photos"])


async def ensure_photo_tables():
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: _md.create_all(c, checkfirst=True))


def _model(listing_type: str):
    model = LISTING_TYPES.get(listing_type)
    if model is None:
        raise HTTPException(status_code=400, detail="listing_type must be 'part' or 'vehicle'")
    return model


async def _listing_for_owner(db: AsyncSession, listing_type: str, listing_id: int, user):
    model = _model(listing_type)
    listing = (await db.execute(select(model).where(model.id == listing_id))).scalars().first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    owners = {getattr(listing, "vendor_id", None), getattr(listing, "seller_id", None)} - {None}
    if user.id not in owners and not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Not your listing")
    return listing


async def _gallery(db: AsyncSession, listing_type: str, listing_id: int, cover: str | None) -> list[dict]:
    rows = (await db.execute(
        select(images).where(images.c.listing_type == listing_type, images.c.listing_id == listing_id)
        .order_by(images.c.position, images.c.id)
    )).all()
    photos = [{"id": r.id, "url": r.url} for r in rows]
    # Listings from before galleries existed only have a cover photo.
    if cover and not any(p["url"] == cover for p in photos):
        photos.insert(0, {"id": None, "url": cover})
    return photos


@router.get("/{listing_type}/{listing_id}")
async def list_photos(listing_type: str, listing_id: int, db: AsyncSession = Depends(get_db)):
    model = _model(listing_type)
    listing = (await db.execute(select(model).where(model.id == listing_id))).scalars().first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"photos": await _gallery(db, listing_type, listing_id, listing.image_url)}


@router.post("/{listing_type}/{listing_id}", status_code=201)
async def add_photo(
    listing_type: str,
    listing_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    from app.routers.upload import _save_and_resize  # shared resize + storage (Cloudinary / R2 / disk)

    listing = await _listing_for_owner(db, listing_type, listing_id, user)
    count = (await db.execute(
        select(func.count(images.c.id)).where(images.c.listing_type == listing_type, images.c.listing_id == listing_id)
    )).scalar() or 0
    if count >= MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f"A listing can have up to {MAX_PHOTOS} photos.")

    url = await run_in_threadpool(_save_and_resize, file)
    res = await db.execute(insert(images).values(
        listing_type=listing_type, listing_id=listing_id, url=url, position=count, created_at=datetime.utcnow()))
    if not listing.image_url:
        listing.image_url = url
    await db.commit()
    return {"id": res.inserted_primary_key[0], "url": url, "cover": listing.image_url == url}


@router.post("/{listing_type}/{listing_id}/cover/{photo_id}")
async def set_cover(
    listing_type: str, listing_id: int, photo_id: int,
    db: AsyncSession = Depends(get_db), user: models.User = Depends(get_current_user),
):
    listing = await _listing_for_owner(db, listing_type, listing_id, user)
    row = (await db.execute(select(images).where(
        images.c.id == photo_id, images.c.listing_type == listing_type, images.c.listing_id == listing_id))).first()
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    listing.image_url = row.url
    first = (await db.execute(select(func.min(images.c.position)).where(
        images.c.listing_type == listing_type, images.c.listing_id == listing_id))).scalar() or 0
    await db.execute(update(images).where(images.c.id == photo_id).values(position=first - 1))
    await db.commit()
    return {"cover": row.url}


@router.delete("/{listing_type}/{listing_id}/{photo_id}")
async def delete_photo(
    listing_type: str, listing_id: int, photo_id: int,
    db: AsyncSession = Depends(get_db), user: models.User = Depends(get_current_user),
):
    listing = await _listing_for_owner(db, listing_type, listing_id, user)
    row = (await db.execute(select(images).where(
        images.c.id == photo_id, images.c.listing_type == listing_type, images.c.listing_id == listing_id))).first()
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    await db.execute(delete(images).where(images.c.id == photo_id))
    if listing.image_url == row.url:
        nxt = (await db.execute(select(images.c.url).where(
            images.c.listing_type == listing_type, images.c.listing_id == listing_id)
            .order_by(images.c.position, images.c.id).limit(1))).first()
        listing.image_url = nxt.url if nxt else None
    await db.commit()
    return {"deleted": photo_id, "cover": listing.image_url}
