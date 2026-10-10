import os
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import get_settings

settings = get_settings()

# Read DATABASE_URL from environment, fall back to SQLite for local dev
DATABASE_URL = os.getenv("DATABASE_URL", settings.DATABASE_URL)

# Render provides postgres:// URLs that need to become postgresql+asyncpg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

print(f"[db.py] Using database: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

sync_database_url = DATABASE_URL
if sync_database_url.startswith("postgresql+asyncpg://"):
    sync_database_url = sync_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
elif sync_database_url.startswith("sqlite+aiosqlite://"):
    sync_database_url = sync_database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)

connect_args = {"check_same_thread": False} if sync_database_url.startswith("sqlite") else {}
sync_engine = create_engine(sync_database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()


async def ensure_vehicle_listing_columns():
    """Add gearbox/fuel columns to vehicle_listings tables created before they existed."""
    async with engine.begin() as connection:
        def get_columns(sync_connection):
            inspector = inspect(sync_connection)
            if not inspector.has_table("vehicle_listings"):
                return None
            return {column["name"] for column in inspector.get_columns("vehicle_listings")}

        existing = await connection.run_sync(get_columns)
        if existing is None:
            return
        for column in ("fuel_type", "transmission"):
            if column not in existing:
                await connection.execute(text(f"ALTER TABLE vehicle_listings ADD COLUMN {column} VARCHAR"))


async def ensure_business_contact_columns():
    """Add business storefront fields to databases created by the older ORM model."""
    async with engine.begin() as connection:
        def get_business_columns(sync_connection):
            inspector = inspect(sync_connection)
            if not inspector.has_table("businesses"):
                return None
            return {column["name"] for column in inspector.get_columns("businesses")}

        existing = await connection.run_sync(get_business_columns)
        if existing is None:
            return

        additions = {
            "whatsapp": "VARCHAR",
            "email": "VARCHAR",
            "logo_url": "VARCHAR",
            "subscription_tier": "VARCHAR NOT NULL DEFAULT 'free'",
        }
        for column, definition in additions.items():
            if column not in existing:
                await connection.execute(text(f"ALTER TABLE businesses ADD COLUMN {column} {definition}"))


def _seed_legacy_schema():
    """Create the legacy compatibility tables and seed a minimal data set expected by the tests."""
    import sys

    module = sys.modules.get("app.models")
    if module is None or not hasattr(module, "User"):
        return
    if os.getenv("SEED_DEMO_DATA", "false").lower() != "true":
        return

    from app.models import User, Vehicle, Part, Supplier, SupplierPart, PartFitment, Listing, ListingPhoto, Order, OrderItem

    if __import__("os").getenv("AUTO_CREATE_TABLES", "false").lower() == "true":
            Base.metadata.create_all(bind=sync_engine)

    with SessionLocal() as session:
        user_count = session.execute(select(func.count()).select_from(User)).scalar() or 0
        if user_count == 0:
            user = User(
                id=1,
                full_name="Demo Seller",
                phone="+23276123001",
                email="seller@example.com",
                hashed_password="test",
                role="seller",
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

            part_fitment = PartFitment(
                id="fitment-1",
                part_id="part-1",
                vehicle_id="veh-1",
                year_from=2003,
                year_to=2008,
                fitment_notes="Fits Corolla 2003-2008",
            )
            session.add(part_fitment)

            supplier_part = SupplierPart(
                id="sp-1",
                supplier_id="supplier-1",
                part_id="part-1",
                price=480.00,
                currency_code="NLE",
                stock=6,
                lead_time_days=0,
            )
            session.add(supplier_part)

            listing = Listing(
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
            )
            session.add(listing)

            session.flush()

            listing_photo = ListingPhoto(
                id="photo-1",
                listing_id="listing-1",
                url="https://example.com/car.jpg",
                position=0,
                is_hero=True,
            )
            session.add(listing_photo)

            order = Order(
                id="order-1",
                buyer_id=1,
                status="completed",
                payment_method="mobile_money",
                payment_status="paid",
                delivery_address="Freetown",
                total_amount=480.00,
                currency_code="NLE",
            )
            session.add(order)

            order_item = OrderItem(
                id="order-item-1",
                order_id="order-1",
                item_type="part",
                supplier_part_id="sp-1",
                quantity=1,
                unit_price=480.00,
                currency_code="NLE",
                estimated_delivery=None,
                fulfillment_status="delivered",
            )
            session.add(order_item)

            session.commit()


_seed_legacy_schema()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
