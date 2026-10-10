from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import Optional

from app.db import get_db
from app import models, schemas
from app.services import search

PART_TEXT = (models.PartListing.name, models.PartListing.category, models.PartListing.compatible_make,
             models.PartListing.compatible_model, models.PartListing.description)

router = APIRouter(prefix="/api/unified", tags=["Unified Search"])


@router.get("/catalog/{catalog_id}")
async def catalog_item(catalog_id: int, db: AsyncSession = Depends(get_db)):
    item = (await db.execute(
        select(models.SupplierCatalog).where(models.SupplierCatalog.id == catalog_id)
    )).scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Catalog part not found")
    return {
        "id": item.id,
        "part_number": item.part_number,
        "category": item.category,
        "vehicle_compatibility": item.vehicle_compatibility,
    }


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
    did_you_mean = None

    # ---------- Local parts ----------
    def parts_query(text):
        query = select(models.PartListing).where(models.PartListing.stock_quantity > 0)
        cond = search.condition(text, PART_TEXT)
        return query.where(cond) if cond is not None else query

    local_q = parts_query(q)
    if q:
        has_any = (await db.execute(select(func.count()).select_from(local_q.subquery()))).scalar()
        if not has_any:
            names = (await db.execute(
                select(models.PartListing.name, models.PartListing.compatible_make, models.PartListing.compatible_model)
                .order_by(models.PartListing.created_at.desc()).limit(500))).all()
            fixed = search.correct(q, search.vocabulary_from(" ".join(filter(None, r)) for r in names))
            if fixed:
                did_you_mean = fixed
                local_q = parts_query(fixed)
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

    local_q = local_q.order_by(models.PartListing.is_featured.desc(), models.PartListing.created_at.desc()).limit(limit).offset(offset)
    local_parts = (await db.execute(local_q)).scalars().all()

    # ---------- Supplier catalog (importable parts) ----------
    # ✅ FIXED: was models.CatalogPart — your class is SupplierCatalog
    catalog_count = 0
    catalog_parts = []

    catalog_q = select(models.SupplierCatalog)
    cat_cond = search.condition(did_you_mean or q, (
        models.SupplierCatalog.part_number, models.SupplierCatalog.name, models.SupplierCatalog.category,
        models.SupplierCatalog.vehicle_compatibility, models.SupplierCatalog.brand_1))
    if cat_cond is not None:
        catalog_q = catalog_q.where(cat_cond)
    if category:
        catalog_q = catalog_q.where(models.SupplierCatalog.category.ilike(f"%{category}%"))

    catalog_count = (await db.execute(
        select(func.count()).select_from(catalog_q.subquery())
    )).scalar() or 0

    catalog_q = catalog_q.limit(limit).offset(offset)
    catalog_parts = (await db.execute(catalog_q)).scalars().all()

    return {
        "total": local_count + catalog_count,
        "did_you_mean": did_you_mean,
        "local_count": local_count,
        "catalog_count": catalog_count,
        "page": page,
        "limit": limit,
        "local_parts": [schemas.PartResponse.model_validate(p).model_dump() for p in local_parts],
        "catalog_parts": [
            {
                "id": c.id,
                "part_number": getattr(c, "part_number", "") or "",
                "name": getattr(c, "name", "") or "",
                "category": getattr(c, "category", "") or "",
                "vehicle_compatibility": getattr(c, "vehicle_compatibility", "") or "",
            }
            for c in catalog_parts
        ],
    }


# ✅ ADDED: /suggest route — your homepage predictive search calls this
@router.get("/suggest")
async def unified_suggest(
    q: str = Query("", min_length=1),
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    suggestions = []
    if not search.words(q):
        return {"suggestions": []}

    # Local parts
    parts_q = select(models.PartListing).where(
        models.PartListing.stock_quantity > 0, search.condition(q, PART_TEXT)).limit(4)
    for p in (await db.execute(parts_q)).scalars().all():
        suggestions.append({
            "type": "part",
            "icon": "🔧",
            "label": p.name,
            "sublabel": ", ".join(x for x in (p.category, p.location) if x),
            "url": f"/part/{p.id}",
        })

    # Vehicles
    vehicles_q = select(models.VehicleListing).where(
        models.VehicleListing.is_sold.is_(False),
        search.condition(q, (models.VehicleListing.title, models.VehicleListing.make, models.VehicleListing.model)),
    ).limit(4)
    for v in (await db.execute(vehicles_q)).scalars().all():
        suggestions.append({
            "type": "vehicle",
            "icon": "🚗",
            "label": v.title or f"{v.year} {v.make} {v.model}",
            "sublabel": ", ".join(x for x in (f"SLL {int(v.price_sll or 0):,}", v.location) if x),
            "url": f"/vehicle/{v.id}",
        })

    # Supplier catalog
    catalog_q = select(models.SupplierCatalog).where(search.condition(q, (
        models.SupplierCatalog.part_number, models.SupplierCatalog.name, models.SupplierCatalog.category,
        models.SupplierCatalog.vehicle_compatibility))).limit(4)
    for c in (await db.execute(catalog_q)).scalars().all():
        suggestions.append({
            "type": "catalog",
            "icon": "🌍",
            "label": c.part_number or "Import",
            "sublabel": c.vehicle_compatibility or c.category or "Import from abroad",
            "url": f"/catalog/{c.id}",
        })

    return {"suggestions": suggestions[:limit]}
