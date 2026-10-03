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
from app.r2_storage import save_to_r2, R2_CONFIGURED
from io import BytesIO

router = APIRouter(prefix="/api/upload", tags=["Upload"])

# Where files are stored (relative to project root)
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Allowed types and max size (5 MB)

import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME', ''),
    api_key=os.getenv('CLOUDINARY_API_KEY', ''),
    api_secret=os.getenv('CLOUDINARY_API_SECRET', ''),
    secure=True,
)

def _cloudinary_configured():
    return bool(os.getenv('CLOUDINARY_CLOUD_NAME') and os.getenv('CLOUDINARY_API_KEY') and os.getenv('CLOUDINARY_API_SECRET'))

def save_to_cloudinary(file_bytes, folder='saloncarparts'):
    result = cloudinary.uploader.upload(file_bytes, folder=folder, resource_type='image')
    return result.get('secure_url', '')

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
        pass  # BytesIO imported at module level
    ext = "jpg"

    # Read and check size
    content = file.file.read()
    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Image larger than 5 MB")

    # Resize in memory (no temp file needed)
    try:
        with Image.open(BytesIO(content)) as img:
            img = img.convert("RGB")
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=82, optimize=True)
            resized_bytes = buf.getvalue()
    except Exception as _e:
        import traceback
        print('UPLOAD PIL ERROR:', traceback.format_exc()[:600])
        raise HTTPException(status_code=400, detail="PIL failed: " + type(_e).__name__ + ": " + str(_e)[:200])

    # Try Cloudinary first, fall back to R2, then disk
    if _cloudinary_configured():
        try:
            url = save_to_cloudinary(resized_bytes)
            if url:
                return url
        except Exception as e:
            print(f"Cloudinary upload failed, falling back: {e}")

    if R2_CONFIGURED:
        try:
            return save_to_r2(resized_bytes, ext, "image/jpeg")
        except Exception as e:
            print(f"R2 upload failed, falling back to disk: {e}")

    # Local disk fallback
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = UPLOAD_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(resized_bytes)
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