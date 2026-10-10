from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc
from typing import List, Optional

from app.db import get_db
from app import models, schemas
from app.routers.admin import require_admin

router = APIRouter(prefix="/api/import-requests", tags=["Import Requests"])


@router.post("/", response_model=schemas.ImportRequestResponse)
async def create_import_request(
    request: schemas.ImportRequestCreate,
    db: AsyncSession = Depends(get_db),
):
    """Customer submits a request for a part to be imported from abroad."""
    new_request = models.ImportRequest(**request.model_dump())
    db.add(new_request)
    await db.commit()
    await db.refresh(new_request)
    return new_request


@router.get("/", response_model=List[schemas.ImportRequestResponse])
async def list_import_requests(
    status: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """Admin view — list all import requests. Filter by status/location."""
    query = select(models.ImportRequest)
    if status:
        query = query.where(models.ImportRequest.status == status)
    if location:
        query = query.where(models.ImportRequest.customer_location.ilike(f"%{location}%"))
    query = query.order_by(desc(models.ImportRequest.created_at)).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def import_stats(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    """Get stats for admin dashboard."""
    total = await db.execute(select(func.count(models.ImportRequest.id)))
    pending = await db.execute(
        select(func.count(models.ImportRequest.id)).where(models.ImportRequest.status == "pending")
    )
    quoted = await db.execute(
        select(func.count(models.ImportRequest.id)).where(models.ImportRequest.status == "quoted")
    )
    delivered = await db.execute(
        select(func.count(models.ImportRequest.id)).where(models.ImportRequest.status == "delivered")
    )
    return {
        "total": total.scalar() or 0,
        "pending": pending.scalar() or 0,
        "quoted": quoted.scalar() or 0,
        "delivered": delivered.scalar() or 0,
    }


@router.get("/{request_id}", response_model=schemas.ImportRequestResponse)
async def get_import_request(request_id: int, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    """Get a single import request."""
    result = await db.execute(
        select(models.ImportRequest).where(models.ImportRequest.id == request_id)
    )
    req = result.scalars().first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


@router.put("/{request_id}", response_model=schemas.ImportRequestResponse)
async def update_import_request(
    request_id: int,
    update: schemas.ImportRequestUpdate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """Admin updates a request (quote price, status, notes, etc.)."""
    result = await db.execute(
        select(models.ImportRequest).where(models.ImportRequest.id == request_id)
    )
    req = result.scalars().first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(req, key, value)

    await db.commit()
    await db.refresh(req)
    return req


@router.delete("/{request_id}")
async def delete_import_request(request_id: int, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    """Admin deletes a request."""
    result = await db.execute(
        select(models.ImportRequest).where(models.ImportRequest.id == request_id)
    )
    req = result.scalars().first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    await db.delete(req)
    await db.commit()
    return {"message": "Deleted", "id": request_id}
