from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.db import Base


# ============================================================
# USER MODEL (Fixes the missing attribute error)
# ============================================================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    vehicles = relationship("VehicleListing", back_populates="seller", cascade="all, delete-orphan")
    businesses = relationship("Business", back_populates="owner", cascade="all, delete-orphan")
    parts = relationship("PartListing", back_populates="seller", cascade="all, delete-orphan")


# ============================================================
# BUSINESS MODEL
# ============================================================
class Business(Base):
    __tablename__ = "businesses"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    business_type = Column(String, nullable=False)  # "store", "dealership", "shipper", "wholesaler"
    status = Column(String, default="pending", nullable=False)  # "pending", "verified", "rejected"
    city = Column(String, nullable=True)
    country = Column(String, default="Sierra Leone", nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    owner = relationship("User", back_populates="businesses")
    vehicles = relationship("VehicleListing", back_populates="business")


# ============================================================
# VEHICLE LISTING MODEL
# ============================================================
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


# ============================================================
# PART LISTING MODEL
# ============================================================
class PartListing(Base):
    __tablename__ = "part_listings"

    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    name = Column(String, nullable=False, index=True)
    category = Column(String, nullable=False, index=True)
    compatible_make = Column(String, nullable=True, index=True)
    price_sll = Column(Float, nullable=False)
    location = Column(String, nullable=True)
    condition = Column(String, default="used", nullable=False)
    description = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    seller = relationship("User", back_populates="parts")
