from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime,
    ForeignKey, Text, Index
)
from sqlalchemy.orm import relationship
from app.db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    phone = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_vendor = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    roles = Column(String, default="buyer")
    garage_cars = relationship("Garage", back_populates="owner", cascade="all, delete-orphan")


class VehicleListing(Base):
    __tablename__ = "vehicle_listings"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    make = Column(String, index=True)
    model = Column(String, index=True)
    year = Column(Integer)
    price_sll = Column(Float)
    price_usd = Column(Float, nullable=True)
    mileage_km = Column(Integer)
    fuel_type = Column(String)
    transmission = Column(String)
    location = Column(String, index=True)
    description = Column(Text, nullable=True)
    image_url = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    is_sold = Column(Boolean, default=False)
    views = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    seller_id = Column(Integer, ForeignKey("users.id"))
    seller = relationship("User")

    # Featured listing support
    is_featured = Column(Boolean, default=False, index=True)
    featured_until = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_vehicle_created_at", "created_at"),
        Index("idx_vehicle_make_model", "make", "model"),
        Index("idx_vehicle_location_created", "location", "created_at"),
        Index("idx_vehicle_featured", "is_featured", "created_at"),
    )


class PartListing(Base):
    __tablename__ = "part_listings"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    category = Column(String, index=True)
    compatible_make = Column(String, nullable=True, index=True)
    compatible_model = Column(String, nullable=True, index=True)
    price_sll = Column(Float)
    stock_quantity = Column(Integer, default=1)
    condition = Column(String)
    location = Column(String, index=True)
    description = Column(Text, nullable=True)
    image_url = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    views = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    vendor_id = Column(Integer, ForeignKey("users.id"))
    vendor = relationship("User")

    # Featured listing support
    is_featured = Column(Boolean, default=False, index=True)
    featured_until = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_part_created_at", "created_at"),
        Index("idx_part_category_created", "category", "created_at"),
        Index("idx_part_make_model", "compatible_make", "compatible_model"),
        Index("idx_part_location_created", "location", "created_at"),
        Index("idx_part_featured", "is_featured", "created_at"),
    )


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(Integer, ForeignKey("users.id"))
    total_amount_sll = Column(Float)
    status = Column(String, default="pending")
    payment_method = Column(String, nullable=True)
    payment_reference = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    buyer = relationship("User")

    __table_args__ = (
        Index("idx_order_created_at", "created_at"),
        Index("idx_order_buyer_created", "buyer_id", "created_at"),
    )


class ImportRequest(Base):
    __tablename__ = "import_requests"
    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String, nullable=False)
    customer_phone = Column(String, nullable=False)
    customer_email = Column(String, nullable=True)
    customer_location = Column(String, nullable=False)
    part_name = Column(String, nullable=False)
    car_make = Column(String, nullable=False)
    car_model = Column(String, nullable=False)
    car_year = Column(String, nullable=False)
    part_number = Column(String, nullable=True)
    quantity = Column(Integer, default=1)
    budget_sll = Column(Float, nullable=True)
    urgency = Column(String, default="Normal")
    notes = Column(Text, nullable=True)
    status = Column(String, default="pending")
    quoted_price_sll = Column(Float, nullable=True)
    supplier_country = Column(String, nullable=True)
    estimated_days = Column(Integer, nullable=True)
    admin_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_import_created_at", "created_at"),
        Index("idx_import_status_created", "status", "created_at"),
    )


class SupplierCatalog(Base):
    __tablename__ = "supplier_catalog"
    id = Column(Integer, primary_key=True, index=True)
    part_number = Column(String, index=True, nullable=False)
    category = Column(String, index=True, nullable=True)
    vehicle_compatibility = Column(String, nullable=True)
    brand_1 = Column(String, nullable=True)
    price_1_sll = Column(Float, nullable=True)
    price_1_cad = Column(Float, nullable=True)
    brand_2 = Column(String, nullable=True)
    price_2_sll = Column(Float, nullable=True)
    price_2_cad = Column(Float, nullable=True)
    brand_3 = Column(String, nullable=True)
    price_3_sll = Column(Float, nullable=True)
    price_3_cad = Column(Float, nullable=True)
    in_stock = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_catalog_created_at", "created_at"),
        Index("idx_catalog_category_created", "category", "created_at"),
    )


class Inquiry(Base):
    __tablename__ = "inquiries"
    id = Column(Integer, primary_key=True, index=True)
    listing_type = Column(String, nullable=False)
    listing_id = Column(Integer, nullable=False)
    listing_title = Column(String, nullable=True)
    buyer_name = Column(String, nullable=False)
    buyer_phone = Column(String, nullable=False)
    buyer_message = Column(Text, nullable=True)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    seller_phone = Column(String, nullable=True)
    status = Column(String, default="new")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_inquiry_created_at", "created_at"),
        Index("idx_inquiry_seller_created", "seller_id", "created_at"),
        Index("idx_inquiry_listing", "listing_type", "listing_id"),
    )


class Garage(Base):
    __tablename__ = "garages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    make = Column(String, nullable=False, index=True)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    nickname = Column(String, nullable=True)
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="garage_cars")

    __table_args__ = (
        Index("idx_garage_user", "user_id"),
        Index("idx_garage_user_primary", "user_id", "is_primary"),
    )
