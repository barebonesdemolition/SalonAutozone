from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from app.db import get_db
from app import models
from app.auth import get_current_user
from app.routers.admin import require_admin

router = APIRouter(prefix="/api/inquiries", tags=["Inquiries"])


# ---------- Schemas ----------
class InquiryCreate(BaseModel):
    listing_type: str  # "vehicle" or "part"
    listing_id: int
    buyer_name: str
    buyer_phone: str
    buyer_message: Optional[str] = None


class InquiryResponse(BaseModel):
    id: int
    listing_type: str
    listing_id: int
    listing_title: Optional[str] = None
    buyer_name: str
    buyer_phone: str
    buyer_message: Optional[str] = None
    seller_id: Optional[int] = None
    seller_phone: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class InquiryUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


# ---------- Endpoints ----------
@router.post("/", response_model=InquiryResponse)
async def create_inquiry(
    inquiry: InquiryCreate,
    db: AsyncSession = Depends(get_db),
):
    """Buyer submits an inquiry about a listing."""
    # Look up the listing to get title, seller info
    listing_title = None
    seller_id = None
    seller_phone = None

    if inquiry.listing_type == "vehicle":
        result = await db.execute(
            select(models.VehicleListing).where(models.VehicleListing.id == inquiry.listing_id)
        )
        listing = result.scalars().first()
        if not listing:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        listing_title = listing.title
        seller_id = listing.seller_id
        seller_phone = listing.contact_phone
    elif inquiry.listing_type == "part":
        result = await db.execute(
            select(models.PartListing).where(models.PartListing.id == inquiry.listing_id)
        )
        listing = result.scalars().first()
        if not listing:
            raise HTTPException(status_code=404, detail="Part not found")
        listing_title = listing.name
        seller_id = listing.vendor_id
        seller_phone = listing.contact_phone
    else:
        raise HTTPException(status_code=400, detail="listing_type must be 'vehicle' or 'part'")

    new_inquiry = models.Inquiry(
        listing_type=inquiry.listing_type,
        listing_id=inquiry.listing_id,
        listing_title=listing_title,
        buyer_name=inquiry.buyer_name,
        buyer_phone=inquiry.buyer_phone,
        buyer_message=inquiry.buyer_message,
        seller_id=seller_id,
        seller_phone=seller_phone,
        status="new",
    )
    db.add(new_inquiry)
    await db.commit()
    await db.refresh(new_inquiry)
    return new_inquiry


@router.get("/", response_model=List[InquiryResponse])
async def list_all_inquiries(
    status: Optional[str] = Query(None),
    listing_type: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """Admin: list all inquiries."""
    query = select(models.Inquiry)
    if status:
        query = query.where(models.Inquiry.status == status)
    if listing_type:
        query = query.where(models.Inquiry.listing_type == listing_type)
    query = query.order_by(desc(models.Inquiry.created_at)).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def inquiry_stats(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    """Admin: quick stats."""
    total = await db.execute(select(func.count(models.Inquiry.id)))
    new = await db.execute(
        select(func.count(models.Inquiry.id)).where(models.Inquiry.status == "new")
    )
    contacted = await db.execute(
        select(func.count(models.Inquiry.id)).where(models.Inquiry.status == "contacted")
    )
    sold = await db.execute(
        select(func.count(models.Inquiry.id)).where(models.Inquiry.status == "sold")
    )
    return {
        "total": total.scalar() or 0,
        "new": new.scalar() or 0,
        "contacted": contacted.scalar() or 0,
        "sold": sold.scalar() or 0,
    }


@router.get("/listing/{listing_type}/{listing_id}", response_model=List[InquiryResponse])
async def list_inquiries_for_listing(
    listing_type: str,
    listing_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """Get all inquiries for a specific listing."""
    result = await db.execute(
        select(models.Inquiry)
        .where(models.Inquiry.listing_type == listing_type)
        .where(models.Inquiry.listing_id == listing_id)
        .order_by(desc(models.Inquiry.created_at))
    )
    return result.scalars().all()


@router.get("/seller/{seller_id}", response_model=List[InquiryResponse])
async def list_inquiries_for_seller(
    seller_id: int,
    db: AsyncSession = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Get all inquiries on a seller's listings. Only that seller (or an admin) may see them."""
    roles = {r.strip().lower() for r in (user.roles or "").split(",")}
    if user.id != seller_id and not (user.is_admin or roles & {"admin", "superadmin"}):
        raise HTTPException(status_code=403, detail="You can only see inquiries on your own listings")
    result = await db.execute(
        select(models.Inquiry)
        .where(models.Inquiry.seller_id == seller_id)
        .order_by(desc(models.Inquiry.created_at))
    )
    return result.scalars().all()


@router.put("/{inquiry_id}", response_model=InquiryResponse)
async def update_inquiry(
    inquiry_id: int,
    update: InquiryUpdate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """Update inquiry status (new → contacted → negotiating → sold → lost)."""
    result = await db.execute(
        select(models.Inquiry).where(models.Inquiry.id == inquiry_id)
    )
    inquiry = result.scalars().first()
    if not inquiry:
        raise HTTPException(status_code=404, detail="Inquiry not found")

    update_data = update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(inquiry, key, value)

    await db.commit()
    await db.refresh(inquiry)
    return inquiry


@router.delete("/{inquiry_id}")
async def delete_inquiry(inquiry_id: int, db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    result = await db.execute(
        select(models.Inquiry).where(models.Inquiry.id == inquiry_id)
    )
    inquiry = result.scalars().first()
    if not inquiry:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    await db.delete(inquiry)
    await db.commit()
    return {"message": "Deleted", "id": inquiry_id}
