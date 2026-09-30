# app/routers/upload.py
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db import get_db
from app.auth import get_current_user
from app import models

router = APIRouter(prefix="/api/upload", tags=["Upload"])

# Where files are stored (relative to project root)
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Allowed types and max size (5 MB)
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE_BYTES = 5 * 1024 * 1024
MAX_DIMENSION = 1200  # px


def _save_and_resize(file: UploadFile) -> str:
    """Save uploaded image, resize, return relative URL path."""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Only JPEG, PNG or WebP images are allowed",
        )

    # Generate unique filename
    ext = file.filename.split(".")[-1].lower() if file.filename else "jpg"
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = UPLOAD_DIR / filename

    # Read and check size
    content = file.file.read()
    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Image larger than 5 MB")

    # Save temporarily then resize
    with open(filepath, "wb") as f:
        f.write(content)

    try:
        with Image.open(filepath) as img:
            img = img.convert("RGB")  # handles PNG with alpha
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
            img.save(filepath, format="JPEG", quality=82, optimize=True)
    except Exception:
        filepath.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid image file")

    # Public URL path (adjust if you serve static files differently)
    return f"/uploads/{filename}"


@router.post("/")
async def upload_image(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """
    Upload a single image.
    Returns: { "url": "/uploads/xxxx.jpg" }
    """
    url = _save_and_resize(file)
    return {"url": url, "success": True}


@router.post("/vehicle/{vehicle_id}")
async def upload_vehicle_photo(
    vehicle_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Upload and attach photo to an existing vehicle listing (owner only)."""
    result = await db.execute(
        select(models.VehicleListing).where(models.VehicleListing.id == vehicle_id)
    )
    vehicle = result.scalars().first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if vehicle.seller_id != current_user.id and not getattr(current_user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Not your listing")

    url = _save_and_resize(file)
    vehicle.image_url = url
    await db.commit()
    await db.refresh(vehicle)

    return {"success": True, "url": url, "vehicle_id": vehicle.id}


@router.post("/part/{part_id}")
async def upload_part_photo(
    part_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Upload and attach photo to an existing part listing (owner only)."""
    result = await db.execute(
        select(models.PartListing).where(models.PartListing.id == part_id)
    )
    part = result.scalars().first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    if part.vendor_id != current_user.id and not getattr(current_user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Not your listing")

    url = _save_and_resize(file)
    part.image_url = url
    await db.commit()
    await db.refresh(part)

    return {"success": True, "url": url, "part_id": part.id}
