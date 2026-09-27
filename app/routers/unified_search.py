from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, or_
from typing import Optional

from app.db import get_db
from app import models, schemas

router = APIRouter(prefix="/api/unified", tags=["Unified Search"])


@router.get("/parts")
async def unified_parts_search(
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    year: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * limit

    # Local parts
    local_q = select(models.PartListing)
    if q:
        like = f"%{q}%"
        local_q = local_q.where(or_(
            models.PartListing.name.ilike(like),
            models.PartListing.category.ilike(like),
            models.PartListing.compatible_make.ilike(like),
            models.PartListing.compatible_model.ilike(like),
        ))
    if category:
        local_q = local_q.where(models.PartListing.category.ilike(f"%{category}%"))
    if make:
        local_q = local_q.where(models.PartListing.compatible_make.ilike(f"%{make}%"))
    if model:
        local_q = local_q.where(models.PartListing.compatible_model.ilike(f"%{model}%"))
    if location:
        local_q = local_q.where(models.PartListing.location.ilike(f"%{location}%"))

    local_count = (await db.execute(
        select(func.count()).select_from(local_q.subquery())
    )).scalar() or 0

    local_q = local_q.order_by(models.PartListing.created_at.desc()).limit(limit).offset(offset)
    local_parts = (await db.execute(local_q)).scalars().all()

    # Catalog (importable) parts
    catalog_count = 0
    catalog_parts = []

    catalog_q = select(models.SupplierCatalog)
    if q:
        like = f"%{q}%"
        catalog_q = catalog_q.where(or_(
            models.SupplierCatalog.part_number.ilike(like),
            models.SupplierCatalog.category.ilike(like),
            models.SupplierCatalog.vehicle_compatibility.ilike(like),
        ))
    if category:
        catalog_q = catalog_q.where(models.SupplierCatalog.category.ilike(f"%{category}%"))

    catalog_count = (await db.execute(
        select(func.count()).select_from(catalog_q.subquery())
    )).scalar() or 0

    catalog_q = catalog_q.limit(limit).offset(offset)
    catalog_parts = (await db.execute(catalog_q)).scalars().all()

    return {
        "total": local_count + catalog_count,
        "local_count": local_count,
        "catalog_count": catalog_count,
        "page": page,
        "limit": limit,
        "local_parts": [schemas.PartResponse.model_validate(p).model_dump() for p in local_parts],
        "catalog_parts": [
            {
                "id": c.id,
                "part_number": c.part_number or "",
                "category": c.category or "",
                "vehicle_compatibility": c.vehicle_compatibility or "",
            }
            for c in catalog_parts
        ],
    }


@router.get("/suggest")
async def unified_suggest(
    q: str = Query("", min_length=1),
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    like = f"%{q}%"
    suggestions = []

    parts_q = select(models.PartListing).where(or_(
        models.PartListing.name.ilike(like),
        models.PartListing.category.ilike(like),
        models.PartListing.compatible_make.ilike(like),
    )).limit(4)
    for p in (await db.execute(parts_q)).scalars().all():
        suggestions.append({
            "type": "part",
            "icon": "🔧",
            "label": p.name,
            "sublabel": f"{p.category or 'Part'} · {p.location or ''}".strip(" ·"),
            "url": f"/part/{p.id}",
        })

    vehicles_q = select(models.VehicleListing).where(or_(
        models.VehicleListing.title.ilike(like),
        models.VehicleListing.make.ilike(like),
        models.VehicleListing.model.ilike(like),
    )).limit(4)
    for v in (await db.execute(vehicles_q)).scalars().all():
        suggestions.append({
            "type": "vehicle",
            "icon": "🚗",
            "label": v.title or f"{v.year} {v.make} {v.model}",
            "sublabel": f"SLL {int(v.price_sll or 0):,} · {v.location or ''}".strip(" ·"),
            "url": f"/vehicle/{v.id}",
        })

    catalog_q = select(models.SupplierCatalog).where(or_(
        models.SupplierCatalog.part_number.ilike(like),
        models.SupplierCatalog.category.ilike(like),
        models.SupplierCatalog.vehicle_compatibility.ilike(like),
    )).limit(4)
    for c in (await db.execute(catalog_q)).scalars().all():
        suggestions.append({
            "type": "catalog",
            "icon": "🌍",
            "label": c.part_number or "Import",
            "sublabel": c.vehicle_compatibility or c.category or "Import from abroad",
            "url": f"/catalog/{c.id}",
        })

    return {"suggestions": suggestions[:limit]}
