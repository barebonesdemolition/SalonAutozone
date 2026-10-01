import traceback
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app import models
from app.db import get_db

router = APIRouter(prefix="/api/search", tags=["Search"])


@router.get("/suggest")
async def suggest(
    q: str = Query(..., min_length=1),
    mode: str = Query("parts", description="'parts', 'vehicles', or 'businesses'"),
    limit: int = Query(8, le=20),
    db: AsyncSession = Depends(get_db),
):
    q_lower = q.lower().strip()
    like_query = f"%{q_lower}%"
    suggestions = []

    try:
        # ============================================================
        # 1. VEHICLE SUGGESTIONS
        # ============================================================
        if mode == "vehicles":
            query = select(models.VehicleListing).where(
                models.VehicleListing.is_sold == False
            ).where(
                or_(
                    models.VehicleListing.title.ilike(like_query),
                    models.VehicleListing.make.ilike(like_query),
                    models.VehicleListing.model.ilike(like_query),
                    models.VehicleListing.location.ilike(like_query),
                    models.VehicleListing.vin.ilike(like_query),
                    models.VehicleListing.country_of_origin.ilike(like_query),
                    models.VehicleListing.condition.ilike(like_query),
                    models.VehicleListing.availability.ilike(like_query),
                    cast(models.VehicleListing.year, String).ilike(like_query),
                )
            ).limit(limit)

            result = await db.execute(query)
            vehicles = result.scalars().all()

            for v in vehicles:
                price_str = f"SLL {int(v.price_sll):,}" if v.price_sll else "Contact for Price"
                
                # Format availability indicator for scam awareness
                avail_label = ""
                if v.availability == "in_transit":
                    avail_label = " • 🚢 In Transit"
                elif v.availability == "on_order":
                    avail_label = " • 📋 On Order"

                transmission_str = getattr(v, "transmission", None) or v.condition.capitalize()

                suggestions.append({
                    "type": "vehicle",
                    "id": v.id,
                    "label": f"{v.title} — {v.location or 'Sierra Leone'}",
                    "sublabel": f"{price_str} • {v.year or ''} • {transmission_str}{avail_label}",
                    "search_text": v.title,
                    "availability": v.availability,
                    "url": f"/vehicle/{v.id}",
                })

        # ============================================================
        # 2. BUSINESS / STOREFRONT SUGGESTIONS
        # ============================================================
        elif mode == "businesses":
            query = select(models.Business).where(
                models.Business.status == "verified"
            ).where(
                or_(
                    models.Business.name.ilike(like_query),
                    models.Business.city.ilike(like_query),
                    models.Business.business_type.ilike(like_query),
                    models.Business.description.ilike(like_query),
                )
            ).limit(limit)

            result = await db.execute(query)
            businesses = result.scalars().all()

            type_labels = {
                "store": "Local Parts Store",
                "dealership": "Car Dealership",
                "shipper": "Shipper / Importer",
                "wholesaler": "Wholesaler",
            }

            for b in businesses:
                b_type = type_labels.get(b.business_type, "Verified Business")
                suggestions.append({
                    "type": "business",
                    "id": b.id,
                    "label": b.name,
                    "sublabel": f"✓ {b_type} • {b.city}, {b.country}",
                    "search_text": b.name,
                    "slug": b.slug,
                    "url": f"/store/{b.slug}",
                })

        # ============================================================
        # 3. PARTS SUGGESTIONS (DEFAULT)
        # ============================================================
        else:
            query = select(models.PartListing).where(
                or_(
                    models.PartListing.name.ilike(like_query),
                    models.PartListing.category.ilike(like_query),
                    models.PartListing.compatible_make.ilike(like_query),
                    models.PartListing.location.ilike(like_query),
                    models.PartListing.condition.ilike(like_query),
                )
            ).limit(limit)

            result = await db.execute(query)
            parts = result.scalars().all()

            for p in parts:
                price_str = f"SLL {int(p.price_sll):,}" if p.price_sll else "Contact for Price"
                suggestions.append({
                    "type": "part",
                    "id": p.id,
                    "label": p.name,
                    "sublabel": f"{price_str} • {p.category or 'General'} • {p.location or 'Sierra Leone'}",
                    "search_text": p.name,
                    "url": f"/part/{p.id}",
                })

        return {"query": q, "mode": mode, "suggestions": suggestions}

    except Exception as e:
        print("SEARCH ERROR:", str(e))
        print(traceback.format_exc())
        return {"query": q, "mode": mode, "suggestions": [], "error": str(e)}


@router.get("/trending")
async def trending(
    mode: str = Query("parts", description="'parts', 'vehicles', or 'businesses'"),
    db: AsyncSession = Depends(get_db),
):
    try:
        if mode == "vehicles":
            result = await db.execute(
                select(
                    models.VehicleListing.make,
                    func.count(models.VehicleListing.id).label("count")
                )
                .where(models.VehicleListing.is_sold == False)
                .group_by(models.VehicleListing.make)
                .order_by(func.count(models.VehicleListing.id).desc())
                .limit(6)
            )
            rows = result.all()
            return {"trending": [{"label": r.make, "count": r.count} for r in rows if r.make]}

        elif mode == "businesses":
            result = await db.execute(
                select(
                    models.Business.business_type,
                    func.count(models.Business.id).label("count")
                )
                .where(models.Business.status == "verified")
                .group_by(models.Business.business_type)
                .order_by(func.count(models.Business.id).desc())
                .limit(6)
            )
            rows = result.all()
            type_labels = {
                "store": "Parts Stores",
                "dealership": "Car Dealerships",
                "shipper": "Car Shippers",
                "wholesaler": "Wholesalers",
            }
            return {
                "trending": [
                    {"label": type_labels.get(r.business_type, r.business_type), "count": r.count}
                    for r in rows if r.business_type
                ]
            }

        else:
            result = await db.execute(
                select(
                    models.PartListing.category,
                    func.count(models.PartListing.id).label("count")
                )
                .group_by(models.PartListing.category)
                .order_by(func.count(models.PartListing.id).desc())
                .limit(6)
            )
            rows = result.all()
            return {"trending": [{"label": r.category, "count": r.count} for r in rows if r.category]}

    except Exception as e:
        return {"trending": [], "error": str(e)}
