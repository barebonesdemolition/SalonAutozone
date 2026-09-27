from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Optional

from app.db import get_db
from app import models, schemas

# 👇 THIS LINE MUST BE HERE — without it, main.py crashes on import
router = APIRouter(prefix="/api/parts", tags=["Parts Marketplace"])


# ============================================================
# CREATE
# ============================================================
@router.post("/", response_model=schemas.PartResponse)
async def create_part_listing(
    part: schemas.PartCreate,
    db: AsyncSession = Depends(get_db),
):
    new_part = models.PartListing(**part.model_dump())
    db.add(new_part)
    await db.commit()
    await db.refresh(new_part)
    return new_part


# ============================================================
# SEARCH (paginated)
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

    offset = (page - 1) * limit
    query = (
        query
        .order_by(models.PartListing.created_at.desc())
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
    return {"total": total.scalar()}


# ============================================================
# GET ONE
# ============================================================
@router.get("/{part_id}", response_model=schemas.PartResponse)
async def get_part(
    part_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.PartListing).where(models.PartListing.id == part_id)
    )
    part = result.scalars().first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    return part


# ============================================================
# UPDATE
# ============================================================
@router.put("/{part_id}", response_model=schemas.PartResponse)
async def update_part(
    part_id: int,
    part: schemas.PartCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.PartListing).where(models.PartListing.id == part_id)
    )
    existing = result.scalars().first()
    if not existing:
        raise HTTPException(status_code=404, detail="Part not found")

    update_data = part.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key == "vendor_id":
            continue  # never overwrite ownership via update
        setattr(existing, key, value)

    await db.commit()
    await db.refresh(existing)
    return existing


# ============================================================
# TRACK VIEW
# ============================================================
@router.post("/{part_id}/view")
async def track_part_view(
    part_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.PartListing).where(models.PartListing.id == part_id)
    )
    part = result.scalars().first()
    if part:
        part.views = (part.views or 0) + 1
        await db.commit()
    return {"ok": True}


# ============================================================
# DELETE
# ============================================================
@router.delete("/{part_id}")
async def delete_part(
    part_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.PartListing).where(models.PartListing.id == part_id)
    )
    part = result.scalars().first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")

    await db.delete(part)
    await db.commit()
    return {"message": "Part deleted"}
