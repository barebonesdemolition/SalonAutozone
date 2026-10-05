from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class VehicleCreate(BaseModel):
    title: str
    make: str
    model: str
    year: int
    price_sll: float
    location: Optional[str] = None
    description: Optional[str] = None
    
    # Dealership / Import Fields
    condition: Optional[str] = "used"          # "new" or "used"
    availability: Optional[str] = "in_stock"    # "in_stock", "in_transit", or "on_order"
    country_of_origin: Optional[str] = None     # e.g., "USA", "Japan", "Germany"
    vin: Optional[str] = None                  # 17-character VIN
    duty_paid: Optional[bool] = False
    arrival_date: Optional[datetime] = None
    supplier_url: Optional[str] = None          # Staff/Internal supplier reference


class VehicleResponse(BaseModel):
    id: int
    seller_id: int
    business_id: Optional[int] = None
    title: str
    make: str
    model: str
    year: int
    price_sll: float
    location: Optional[str] = None
    description: Optional[str] = None
    
    # Dealership / Import Fields
    condition: Optional[str] = "used"
    availability: Optional[str] = "in_stock"
    country_of_origin: Optional[str] = None
    vin: Optional[str] = None
    duty_paid: Optional[bool] = False
    arrival_date: Optional[datetime] = None
    supplier_url: Optional[str] = None
    
    # Listing Meta
    is_sold: Optional[bool] = False
    is_featured: Optional[bool] = False
    featured_until: Optional[datetime] = None
    views: Optional[int] = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =====================================================================
# Added 2026-10-01 — PartCreate / PartResponse used by parts.py
# =====================================================================

class PartCreate(BaseModel):
    name: str
    category: str
    compatible_make: str | None = None
    compatible_model: str | None = None
    price_sll: float
    price_usd: float | None = None
    location: str | None = None
    contact_phone: str | None = None
    image_url: str | None = None
    stock_quantity: int = 1
    condition: str = "used"
    description: str | None = None


class PartResponse(BaseModel):
    id: int
    vendor_id: int | None = None
    name: str
    category: str
    compatible_make: str | None = None
    compatible_model: str | None = None
    price_sll: float
    price_usd: float | None = None
    location: str | None = None
    contact_phone: str | None = None
    image_url: str | None = None
    stock_quantity: int = 1
    condition: Optional[str] = "used"
    description: str | None = None
    is_featured: Optional[bool] = False
    featured_until: datetime | None = None
    views: Optional[int] = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ImportRequestCreate(BaseModel):
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_location: str | None = None
    part_name: str | None = None
    car_make: str | None = None
    car_model: str | None = None
    car_year: int | None = None
    quantity: int = 1
    budget_sll: float | None = None
    urgency: str | None = None
    notes: str | None = None
    status: str = "pending"


class ImportRequestUpdate(BaseModel):
    status: str | None = None
    quoted_price_sll: float | None = None
    estimated_days: int | None = None
    supplier_country: str | None = None
    admin_notes: str | None = None
    notes: str | None = None


class ImportRequestResponse(BaseModel):
    id: int
    user_id: int | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_location: str | None = None
    part_name: str | None = None
    car_make: str | None = None
    car_model: str | None = None
    car_year: int | None = None
    quantity: int = 1
    budget_sll: float | None = None
    urgency: str | None = None
    notes: str | None = None
    status: str = "pending"
    quoted_price_sll: float | None = None
    estimated_days: int | None = None
    supplier_country: str | None = None
    admin_notes: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GarageCarResponse(BaseModel):
    id: int
    make: str | None = None
    model: str | None = None
    year: int | None = None
    vin: str | None = None
    nickname: str | None = None
    is_primary: bool = False

    class Config:
        from_attributes = True
