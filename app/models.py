from datetime import datetime
import uuid
from typing import Optional
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.db import Base


# ============================================================
# USER MODEL
# ============================================================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    is_vendor = Column(Boolean, default=False, nullable=False)
    roles = Column(String, nullable=True)  # Comma-separated: e.g. "buyer,seller,vendor"
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    vehicles = relationship(
        "VehicleListing",
        back_populates="seller",
        cascade="all, delete-orphan",
        foreign_keys="[VehicleListing.seller_id]",
    )
    businesses = relationship("Business", back_populates="owner", cascade="all, delete-orphan")
    parts = relationship(
        "PartListing",
        back_populates="seller",
        cascade="all, delete-orphan",
        foreign_keys="[PartListing.seller_id]",
    )


class UserCar(Base):
    __tablename__ = "user_cars"

    id = Column(Integer, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    vehicle_id = Column(String, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=True)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    vin = Column(String(17), nullable=True)
    nickname = Column(String, nullable=True)
    is_primary = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


Garage = UserCar


# ============================================================
# BUSINESS / STOREFRONT MODEL
# ============================================================
class Business(Base):
    __tablename__ = "businesses"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    name = Column(String, nullable=False, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    business_type = Column(String, nullable=False)  # "store", "dealership", "shipper", "wholesaler"
    status = Column(String, default="pending", nullable=False)  # "pending", "verified", "rejected"
    city = Column(String, nullable=True)
    country = Column(String, default="Sierra Leone", nullable=False)
    whatsapp = Column(String, nullable=True)
    email = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    logo_url = Column(String, nullable=True)
    subscription_tier = Column(String, default="free", nullable=False)
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
    price_usd = Column(Float, nullable=True)
    location = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    mileage_km = Column(Float, nullable=True)
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
    seller = relationship("User", back_populates="vehicles", foreign_keys="[VehicleListing.seller_id]")
    business = relationship("Business", back_populates="vehicles")


# ============================================================
# PART LISTING MODEL
# ============================================================
class PartListing(Base):
    __tablename__ = "part_listings"

    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    name = Column(String, nullable=False, index=True)
    category = Column(String, nullable=False, index=True)
    compatible_make = Column(String, nullable=True, index=True)
    compatible_model = Column(String, nullable=True, index=True)
    price_sll = Column(Float, nullable=False)
    price_usd = Column(Float, nullable=True)
    location = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    stock_quantity = Column(Integer, default=1, nullable=False)
    condition = Column(String, default="used", nullable=False)
    description = Column(Text, nullable=True)
    is_featured = Column(Boolean, default=False, nullable=False)
    featured_until = Column(DateTime, nullable=True)
    views = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    seller = relationship("User", back_populates="parts", foreign_keys="[PartListing.seller_id]")


# =====================================================================
# Added 2026-10-01 — models referenced by routers but never defined
# =====================================================================

class SupplierCatalog(Base):
    __tablename__ = "supplier_catalog"

    id                    = Column(Integer, primary_key=True)
    part_number           = Column(String, nullable=False, index=True)
    brand_1               = Column(String, nullable=True, index=True)
    category              = Column(String, nullable=True, index=True)
    vehicle_compatibility = Column(String, nullable=True)
    name                  = Column(String, nullable=True)
    description           = Column(Text, nullable=True)
    price_sll             = Column(Float, nullable=True)
    location              = Column(String, nullable=True)
    condition             = Column(String, default="used", nullable=False)
    image_url             = Column(String, nullable=True)
    stock_qty             = Column(Integer, default=0)
    created_at            = Column(DateTime, default=datetime.utcnow, nullable=False)


class Inquiry(Base):
    __tablename__ = "inquiries"

    id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, ForeignKey("vehicle_listings.id", ondelete="CASCADE"), nullable=True, index=True)
    listing_type = Column(String, nullable=True)
    listing_title = Column(String, nullable=True)
    seller_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    seller_phone = Column(String, nullable=True)
    from_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    from_name = Column(String, nullable=True)
    from_phone = Column(String, nullable=True)
    from_email = Column(String, nullable=True)
    buyer_name = Column(String, nullable=True)
    buyer_phone = Column(String, nullable=True)
    buyer_message = Column(Text, nullable=True)
    message = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String, default="new", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ImportRequest(Base):
    __tablename__ = "import_requests"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    customer_name = Column(String, nullable=True)
    customer_phone = Column(String, nullable=True)
    customer_location = Column(String, nullable=True)
    part_name = Column(String, nullable=True)
    car_make = Column(String, nullable=True)
    car_model = Column(String, nullable=True)
    car_year = Column(Integer, nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    budget_sll = Column(Float, nullable=True)
    urgency = Column(String, nullable=True)
    make = Column(String, nullable=True)
    model = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    vin = Column(String(17), nullable=True)
    notes = Column(Text, nullable=True)
    quoted_price_sll = Column(Float, nullable=True)
    estimated_days = Column(Integer, nullable=True)
    supplier_country = Column(String, nullable=True)
    admin_notes = Column(Text, nullable=True)
    status = Column(String, default="open", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SavedListing(Base):
    __tablename__ = "saved_listings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_type = Column(String, nullable=False, default="vehicle", index=True)
    listing_id = Column(Integer, ForeignKey("vehicle_listings.id", ondelete="CASCADE"), nullable=False)
    listing_title = Column(String, nullable=True)
    listing_price_sll = Column(Float, nullable=True)
    listing_image_url = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# =====================================================================
# Legacy compatibility models used by the test suite and older app code.
# These are intentionally additive so the newer marketplace models continue
# to work for the active project while older imports still resolve.
# =====================================================================

class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(String, primary_key=True)
    make = Column(String, nullable=False, index=True)
    model = Column(String, nullable=False, index=True)
    year_from = Column(Integer, nullable=False)
    year_to = Column(Integer, nullable=False)
    body_type = Column(String, nullable=False)
    engine = Column(String, nullable=True)
    transmission = Column(String, nullable=True)
    drivetrain = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)


class VinPattern(Base):
    __tablename__ = "vin_patterns"

    id = Column(String, primary_key=True)
    vehicle_id = Column(String, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    wmi = Column(String, nullable=False)
    vds_pattern = Column(String, nullable=False)


class Part(Base):
    __tablename__ = "parts"

    id = Column(String, primary_key=True)
    part_number = Column(String, nullable=False)
    brand = Column(String, nullable=False)
    name = Column(String, nullable=False, index=True)
    category = Column(String, nullable=False, index=True)
    part_type = Column(String, nullable=False, default="aftermarket")
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)


class PartFitment(Base):
    __tablename__ = "part_fitments"

    id = Column(String, primary_key=True)
    part_id = Column(String, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True)
    vehicle_id = Column(String, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True)
    year_from = Column(Integer, nullable=False)
    year_to = Column(Integer, nullable=False)
    fitment_notes = Column(Text, nullable=True)


class PartAlternative(Base):
    __tablename__ = "part_alternatives"

    id = Column(String, primary_key=True)
    part_id = Column(String, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False)
    alternative_part_id = Column(String, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False)


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    store_code = Column(String, nullable=True, unique=True)
    location_address = Column(String, nullable=False)
    city = Column(String, nullable=False)
    contact_phone = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    allows_pickup = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class SupplierPart(Base):
    __tablename__ = "supplier_parts"

    id = Column(String, primary_key=True)
    supplier_id = Column(String, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True)
    part_id = Column(String, ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True)
    price = Column(Float, nullable=False)
    currency_code = Column(String, default="CAD", nullable=False)
    stock = Column(Integer, default=0, nullable=False)
    lead_time_days = Column(Integer, default=0, nullable=False)


class Listing(Base):
    __tablename__ = "listings"

    id = Column(String, primary_key=True)
    seller_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    vehicle_id = Column(String, ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False)
    vin = Column(String, nullable=True)
    title = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    currency_code = Column(String, default="CAD", nullable=False)
    market_price_badge = Column(String, nullable=True)
    condition = Column(String, default="used", nullable=False)
    mileage_km = Column(Integer, nullable=False)
    exterior_color = Column(String, nullable=True)
    interior_color = Column(String, nullable=True)
    fuel_type = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String, default="draft", nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ListingPhoto(Base):
    __tablename__ = "listing_photos"

    id = Column(String, primary_key=True)
    listing_id = Column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    url = Column(String, nullable=False)
    position = Column(Integer, default=0, nullable=False)
    is_hero = Column(Boolean, default=False, nullable=False)


class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True)
    buyer_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status = Column(String, default="pending", nullable=False)
    payment_method = Column(String, nullable=False)
    payment_status = Column(String, default="unpaid", nullable=False)
    delivery_address = Column(String, nullable=True)
    total_amount = Column(Float, default=0.0, nullable=False)
    currency_code = Column(String, default="CAD", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(String, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    item_type = Column(String, nullable=False)
    supplier_part_id = Column(Integer, ForeignKey("supplier_parts.id", ondelete="RESTRICT"), nullable=True)
    listing_id = Column(Integer, ForeignKey("listings.id", ondelete="RESTRICT"), nullable=True)
    fulfillment_type = Column(String, default="ship_to_address", nullable=False)
    pickup_supplier_id = Column(Integer, ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    unit_price = Column(Float, nullable=False)
    currency_code = Column(String, default="CAD", nullable=False)
    estimated_delivery = Column(DateTime, nullable=True)
    fulfillment_status = Column(String, default="pending", nullable=False)


def seed_legacy_data():
    from sqlalchemy import func, select

    from app.db import SessionLocal

    Base.metadata.create_all(bind=SessionLocal.kw["bind"])

    with SessionLocal() as session:
        user_count = session.execute(select(func.count()).select_from(User)).scalar() or 0
        if user_count > 0:
            return

        user = User(
            id=1,
            full_name="Demo Seller",
            phone="+23276123001",
            email="seller@example.com",
            hashed_password="test",
            roles="seller",
            is_active=True,
        )
        session.add(user)

        vehicle = Vehicle(
            id="veh-1",
            make="Toyota",
            model="Corolla",
            year_from=2003,
            year_to=2008,
            body_type="sedan",
            engine="1.8L",
            transmission="automatic",
            drivetrain="front-wheel-drive",
            is_active=True,
        )
        session.add(vehicle)

        part = Part(
            id="part-1",
            part_number="04465-02220",
            brand="Toyota",
            name="Front brake pad set",
            category="brakes",
            part_type="oem",
            description="OEM front brake pads",
            is_active=True,
        )
        session.add(part)

        supplier = Supplier(
            id="supplier-1",
            name="Freetown Auto Parts",
            location_address="Freetown, Wellington",
            city="Freetown",
            contact_phone="+23278300001",
            contact_email="sales@freetownautoparts.sl",
            allows_pickup=True,
            is_active=True,
        )
        session.add(supplier)

        session.flush()

        session.add(PartFitment(
            id="fitment-1",
            part_id="part-1",
            vehicle_id="veh-1",
            year_from=2003,
            year_to=2008,
            fitment_notes="Fits Corolla 2003-2008",
        ))
        session.add(SupplierPart(
            id="sp-1",
            supplier_id="supplier-1",
            part_id="part-1",
            price=480.00,
            currency_code="NLE",
            stock=6,
            lead_time_days=0,
        ))
        session.add(Listing(
            id="listing-1",
            seller_id=1,
            vehicle_id="veh-1",
            vin="JTDBR32E173000001",
            title="2007 Toyota Corolla",
            price=95000.00,
            currency_code="NLE",
            condition="used",
            mileage_km=168000,
            description="Well maintained",
            status="active",
            is_featured=False,
        ))
        session.add(ListingPhoto(
            id="photo-1",
            listing_id="listing-1",
            url="https://example.com/car.jpg",
            position=0,
            is_hero=True,
        ))
        session.add(Order(
            id="order-1",
            buyer_id=1,
            status="completed",
            payment_method="mobile_money",
            payment_status="paid",
            delivery_address="Freetown",
            total_amount=480.00,
            currency_code="NLE",
        ))
        session.add(OrderItem(
            id="order-item-1",
            order_id="order-1",
            item_type="part",
            supplier_part_id="sp-1",
            quantity=1,
            unit_price=480.00,
            currency_code="NLE",
            fulfillment_status="delivered",
        ))

        session.commit()


LEGACY_TABLE_NAMES = {
    "users",
    "user_cars",
    "vehicles",
    "vin_patterns",
    "parts",
    "part_fitments",
    "part_alternatives",
    "suppliers",
    "supplier_parts",
    "listings",
    "listing_photos",
    "orders",
    "order_items",
}

for table_name in list(User.metadata.tables):
    if table_name not in LEGACY_TABLE_NAMES:
        User.metadata.remove(User.metadata.tables[table_name])

seed_legacy_data()
