import csv
import io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc, or_
from typing import List, Optional
from pydantic import BaseModel

from app.db import get_db
from app import models
from app.routers.admin import require_admin

router = APIRouter(prefix="/api/catalog", tags=["Supplier Catalog"])


# ---------- Schemas ----------
class CatalogItem(BaseModel):
    id: int
    part_number: str
    category: Optional[str] = None
    vehicle_compatibility: Optional[str] = None
    brand_1: Optional[str] = None
    price_1_sll: Optional[float] = None
    price_1_cad: Optional[float] = None
    in_stock: bool

    class Config:
        from_attributes = True


class CatalogStats(BaseModel):
    total_parts: int
    categories: int
    vehicles: int


# ---------- CSV Parser ----------
KNOWN_CATEGORIES = {
    "clutch spring", "spar plug wire", "spark plug wire",
    "ignition coil", "coil", "auto spring sensor", "lubricant",
    "lubricants", "brake", "brakes", "engine", "suspension",
    "electrical", "transmission", "body", "interior",
    "tires & wheels", "tires", "accessories", "exhaust", "filters",
}

HEADER_KEYWORDS = {"part number", "partnumber", "part_no"}


def is_header_row(row: list) -> bool:
    if not row:
        return False
    first = (row[0] or "").strip().lower()
    return first in HEADER_KEYWORDS


def is_category_row(row: list) -> bool:
    """A category row has text only in column 0, everything else empty."""
    if not row or not row[0] or not row[0].strip():
        return False
    first = row[0].strip().lower()
    if first in KNOWN_CATEGORIES:
        return True
    # If row has only 1 non-empty cell and it's not a header/part number, treat as category
    non_empty = [c for c in row if c and c.strip()]
    if len(non_empty) == 1 and "-" not in first and first not in HEADER_KEYWORDS:
        return True
    return False


def is_part_row(row: list) -> bool:
    """A real part row has a part number in column 0."""
    if not row or not row[0]:
        return False
    first = (row[0] or "").strip()
    if not first or first.lower() in HEADER_KEYWORDS:
        return False
    if first.lower() in KNOWN_CATEGORIES:
        return False
    return True


def parse_price(value: str) -> Optional[float]:
    if not value:
        return None
    cleaned = "".join(c for c in str(value) if c.isdigit() or c == ".")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_csv(content: str):
    """Parse the supplier CSV content and return a list of part dicts."""
    reader = csv.reader(io.StringIO(content))
    rows = [row for row in reader]

    parts = []
    current_category = None

    for row in rows:
        if not row or not any(c and c.strip() for c in row):
            continue  # skip blank rows

        # Category header
        if is_category_row(row):
            cat_name = row[0].strip()
            # Fix common typos
            cat_name = cat_name.replace("Spar Plug", "Spark Plug")
            current_category = cat_name
            continue

        # Column header row
        if is_header_row(row):
            continue

        # Part row
        if is_part_row(row):
            part = {
                "part_number": (row[0] or "").strip(),
                "vehicle_compatibility": (row[1] or "").strip() if len(row) > 1 else None,
                "brand_1": (row[2] or "").strip() if len(row) > 2 and row[2] else None,
                "price_1_sll": parse_price(row[3]) if len(row) > 3 else None,
                "price_1_cad": parse_price(row[4]) if len(row) > 4 else None,
                "brand_2": (row[5] or "").strip() if len(row) > 5 and row[5] else None,
                "price_2_sll": parse_price(row[6]) if len(row) > 6 else None,
                "price_2_cad": parse_price(row[7]) if len(row) > 7 else None,
                "brand_3": (row[8] or "").strip() if len(row) > 8 and row[8] else None,
                "price_3_sll": parse_price(row[9]) if len(row) > 9 else None,
                "price_3_cad": parse_price(row[10]) if len(row) > 10 else None,
                "category": current_category,
            }
            # Only add if part_number is not empty
            if part["part_number"]:
                parts.append(part)

    return parts


# ---------- Endpoints ----------
@router.post("/upload")
async def upload_catalog(
    file: UploadFile = File(...),
    replace: bool = Query(True, description="If true, wipes existing catalog before importing"),
    _: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload a supplier CSV file to import into the catalog."""
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception:
            raise HTTPException(status_code=400, detail="Could not decode CSV file")

    parts = parse_csv(text)

    if not parts:
        raise HTTPException(status_code=400, detail="No parts found in CSV. Check the format.")

    # Optionally wipe existing catalog
    if replace:
        existing = await db.execute(select(models.SupplierCatalog))
        for row in existing.scalars().all():
            await db.delete(row)
        await db.commit()

    imported = 0
    skipped = 0
    for p in parts:
        # Skip duplicates in same import
        result = await db.execute(
            select(models.SupplierCatalog).where(
                models.SupplierCatalog.part_number == p["part_number"]
            )
        )
        if result.scalars().first() and not replace:
            skipped += 1
            continue

        item = models.SupplierCatalog(**p)
        db.add(item)
        imported += 1

    await db.commit()

    return {
        "message": "Catalog imported successfully",
        "imported": imported,
        "skipped": skipped,
        "total_parts": len(parts),
    }


@router.get("/stats", response_model=CatalogStats)
async def catalog_stats(db: AsyncSession = Depends(get_db)):
    """Get stats about the current catalog."""
    total = await db.execute(select(func.count(models.SupplierCatalog.id)))
    cats = await db.execute(
        select(func.count(func.distinct(models.SupplierCatalog.category)))
    )
    vehicles = await db.execute(
        select(func.count(func.distinct(models.SupplierCatalog.vehicle_compatibility)))
    )
    return {
        "total_parts": total.scalar() or 0,
        "categories": cats.scalar() or 0,
        "vehicles": vehicles.scalar() or 0,
    }


@router.get("/search")
async def catalog_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Search the supplier catalog by part number, vehicle, or category."""
    q_lower = f"%{q.lower().strip()}%"
    result = await db.execute(
        select(models.SupplierCatalog)
        .where(
            or_(
                models.SupplierCatalog.part_number.ilike(q_lower),
                models.SupplierCatalog.vehicle_compatibility.ilike(q_lower),
                models.SupplierCatalog.category.ilike(q_lower),
                models.SupplierCatalog.brand_1.ilike(q_lower),
            )
        )
        .limit(limit)
    )
    items = result.scalars().all()
    return {
        "query": q,
        "count": len(items),
        "items": [
            {
                "id": i.id,
                "part_number": i.part_number,
                "category": i.category,
                "vehicle_compatibility": i.vehicle_compatibility,
                "brand_1": i.brand_1,
                "price_1_sll": i.price_1_sll,
                "price_1_cad": i.price_1_cad,
                "in_stock": i.in_stock,
            }
            for i in items
        ],
    }


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)):
    """List all categories in the catalog."""
    result = await db.execute(
        select(models.SupplierCatalog.category, func.count(models.SupplierCatalog.id).label("count"))
        .where(models.SupplierCatalog.category.isnot(None))
        .group_by(models.SupplierCatalog.category)
        .order_by(func.count(models.SupplierCatalog.id).desc())
    )
    rows = result.all()
    return {"categories": [{"name": r.category, "count": r.count} for r in rows]}


@router.get("/all")
async def list_all(
    category: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """List all catalog parts, optionally filtered by category."""
    query = select(models.SupplierCatalog)
    if category:
        query = query.where(models.SupplierCatalog.category == category)
    query = query.order_by(desc(models.SupplierCatalog.created_at)).limit(limit)
    result = await db.execute(query)
    items = result.scalars().all()
    return {
        "count": len(items),
        "items": [
            {
                "id": i.id,
                "part_number": i.part_number,
                "category": i.category,
                "vehicle_compatibility": i.vehicle_compatibility,
            }
            for i in items
        ],
    }
