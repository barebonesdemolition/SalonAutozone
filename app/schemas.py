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
    condition: str
    availability: str
    country_of_origin: Optional[str] = None
    vin: Optional[str] = None
    duty_paid: bool
    arrival_date: Optional[datetime] = None
    supplier_url: Optional[str] = None
    
    # Listing Meta
    is_sold: bool
    is_featured: bool
    featured_until: Optional[datetime] = None
    views: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
