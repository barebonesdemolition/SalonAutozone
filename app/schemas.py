from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional


# --- Vehicle Schemas ---

class VehicleBase(BaseModel):
    title: str
    make: str
    model: str
    year: int
    price_sll: float
    price_usd: Optional[float] = None
    mileage_km: int
    fuel_type: str
    transmission: str
    location: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    contact_phone: Optional[str] = None


class VehicleCreate(VehicleBase):
    # seller_id is set by the backend from the logged-in user — do not require it
    seller_id: Optional[int] = None


class VehicleResponse(VehicleBase):
    id: int
    is_sold: bool = False
    is_featured: bool = False
    featured_until: Optional[datetime] = None
    views: int = 0
    seller_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- Part Schemas ---

class PartBase(BaseModel):
    name: str
    category: str
    compatible_make: Optional[str] = None
    compatible_model: Optional[str] = None
    price_sll: float
    stock_quantity: int = 1
    condition: str
    location: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    contact_phone: Optional[str] = None


class PartCreate(PartBase):
    # vendor_id is set by the backend from the logged-in user
    vendor_id: Optional[int] = None


class PartResponse(PartBase):
    id: int
    is_featured: bool = False
    featured_until: Optional[datetime] = None
    views: int = 0
    vendor_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- Import Request Schemas ---

class ImportRequestCreate(BaseModel):
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    customer_location: str
    part_name: str
    car_make: str
    car_model: str
    car_year: str
    part_number: Optional[str] = None
    quantity: int = 1
    budget_sll: Optional[float] = None
    urgency: str = "Normal"
    notes: Optional[str] = None


class ImportRequestResponse(ImportRequestCreate):
    id: int
    status: str
    quoted_price_sll: Optional[float] = None
    supplier_country: Optional[str] = None
    estimated_days: Optional[int] = None
    admin_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ImportRequestUpdate(BaseModel):
    status: Optional[str] = None
    quoted_price_sll: Optional[float] = None
    supplier_country: Optional[str] = None
    estimated_days: Optional[int] = None
    admin_notes: Optional[str] = None
