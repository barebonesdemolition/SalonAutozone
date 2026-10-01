from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base

class VehicleListing(Base):
    __tablename__ = "vehicle_listings"

    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    business_id = Column(Integer, ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True, index=True)
    
    title = Column(String, nullable=False)
    make = Column(String, nullable=False, index=True)
    model = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=False)
    price_sll = Column(Float, nullable=False)
    location = Column(String, nullable=True)
    description = Column(Text, nullable=True)

    # Dealership & Import Specifics
    condition = Column(String, default="used", nullable=False)        # "new", "used"
    availability = Column(String, default="in_stock", nullable=False) # "in_stock", "in_transit", "on_order"
    country_of_origin = Column(String, nullable=True)
    vin = Column(String(17), nullable=True, index=True)
    duty_paid = Column(Boolean, default=False, nullable=False)
    arrival_date = Column(DateTime, nullable=True)
    supplier_url = Column(String, nullable=True)

    # Status & Analytics
    is_sold = Column(Boolean, default=False, nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)
    featured_until = Column(DateTime, nullable=True)
    views = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    seller = relationship("User", back_populates="vehicles")
    business = relationship("Business", back_populates="vehicles")
