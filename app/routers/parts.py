from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, or_
from typing import List, Optional
from datetime import datetime, timedelta

from app.db import get_db
from app import models, schemas

# ---------------------------------------------------------------------------
# Auth – change this import to match your real auth module
# ---------------------------------------------------------------------------
from app.auth import get_current_user   # must return an object with .id and optionally .is_admin

router = APIRouter(prefix="/api/parts", tags=["Parts Marketplace"])


# ============================================================
# Helpers
# ============================================================

async def _get_part_or_404(db: AsyncSession, part_id: int) -> models.PartListing:
    result = await db.execute(
        select(models.PartListing).where(models.PartListing.id == part_id)
    )
    part = result.scalars().first()
    if not part:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Part not found")
    return part


def _ensure_owner_or_admin(part: models.PartListing, user) -> None:
    """Raise 403 unless the user owns the listing or is an admin."""
    is_owner = getattr(part, "vendor_id", None) == user.id
    is_admin = getattr(user, "is_admin", False)
    if not (is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this listing",
        )


# ============================================================
# CREATE
# ============================================================

@router.post("/", response_model=schemas.PartResponse, status_code=status.HTTP_201_CREATED)
async def create_part_listing(
    part: schemas.PartCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    data = part.model_dump(exclude={"vendor_id"}, exclude_unset=True)
    # Never trust vendor_id from the client
    data["vendor_id"] = current_user.id

    new_part = models.PartListing(**data)
    db.add(new_part)
    await db.commit()
    await db.refresh(new_part)
    return new_part


# ============================================================
# SEARCH (paginated, featured first)
# ============================================================

@router.get("/", response_model=List[schemas.PartResponse])
async def search_parts(
    name: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    compatible_make: Optional[str] = Query(None),
    compatible_model: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    condition: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    featured: Optional[bool] = Query(None, description="Filter by featured status"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(models.PartListing)

    if name:
        query = query.where(models.PartListing.name.ilike(f"%{name}%"))
    if category:
        query = query.where(models.PartListing.category.ilike(f"%{category}%"))
    if compatible_make:
        query = query.where(models.PartListing.compatible_make.ilike(f"%{compatible_make}%"))
    if compatible_model:
        query = query.where(models.PartListing.compatible_model.ilike(f"%{compatible_model}%"))
    if location:
        query = query.where(models.PartListing.location.ilike(f"%{location}%"))
    if condition:
        query = query.where(models.PartListing.condition.ilike(f"%{condition}%"))
    if min_price is not None:
        query = query.where(models.PartListing.price_sll >= min_price)
    if max_price is not None:
        query = query.where(models.PartListing.price_sll <= max_price)
    if featured is not None:
        query = query.where(models.PartListing.is_featured == featured)

    offset = (page - 1) * limit
    query = (
        query
        .order_by(
            models.PartListing.is_featured.desc(),
            models.PartListing.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(query)
    return result.scalars().all()


# ============================================================
# STATS
# ============================================================

@router.get("/stats/count")
async def get_part_stats(db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(models.PartListing.id)))
    return {"total": total.scalar() or 0}


# ============================================================
# GET ONE
# ============================================================

@router.get("/{part_id}", response_model=schemas.PartResponse)
async def get_part(
    part_id: int,
    db: AsyncSession = Depends(get_db),
):
    return await _get_part_or_404(db, part_id)


# ============================================================
# UPDATE
# ============================================================

@router.put("/{part_id}", response_model=schemas.PartResponse)
async def update_part(
    part_id: int,
    part: schemas.PartCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    existing = await _get_part_or_404(db, part_id)
    _ensure_owner_or_admin(existing, current_user)

    update_data = part.model_dump(exclude={"vendor_id"}, exclude_unset=True)
    for key, value in update_data.items():
        setattr(existing, key, value)

    await db.commit()
    await db.refresh(existing)
    return existing


# ============================================================
# DELETE
# ============================================================

@router.delete("/{part_id}", status_code=status.HTTP_200_OK)
async def delete_part(
    part_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    part = await _get_part_or_404(db, part_id)
    _ensure_owner_or_admin(part, current_user)

    await db.delete(part)
    await db.commit()
    return {"message": "Part deleted", "part_id": part_id}


# ============================================================
# PROMOTE / UNPROMOTE
# ============================================================

@router.post("/{part_id}/promote")
async def promote_part(
    part_id: int,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Feature a part listing.
    Currently only checks ownership/admin.
    TODO: Gate behind real payment verification (Orange Money) or make admin-only.
    """
    part = await _get_part_or_404(db, part_id)
    _ensure_owner_or_admin(part, current_user)

    now = datetime.utcnow()
    base = part.featured_until if (part.featured_until and part.featured_until > now) else now
    part.featured_until = base + timedelta(days=days)
    part.is_featured = True

    await db.commit()
    await db.refresh(part)

    return {
        "success": True,
        "part_id": part.id,
        "is_featured": part.is_featured,
        "featured_until": part.featured_until.isoformat() if part.featured_until else None,
    }


@router.post("/{part_id}/unpromote")
async def unpromote_part(
    part_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    part = await _get_part_or_404(db, part_id)
    _ensure_owner_or_admin(part, current_user)

    part.is_featured = False
    part.featured_until = None
    await db.commit()
    return {"success": True, "part_id": part.id, "is_featured": False}


# ============================================================
# TRACK VIEW (public)
# ============================================================

@router.post("/{part_id}/view")
async def track_part_view(
    part_id: int,
    db: AsyncSession = Depends(get_db),
):
    part = await _get_part_or_404(db, part_id)
    part.views = (part.views or 0) + 1
    await db.commit()
    return {"ok": True}
