import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Order, OrderItem, User, Vehicle
from app.businesses import _public
from app.models import Business
from app.routers.garage import GarageCar


def test_orm_models_match_seeded_schema():
    assert set(User.metadata.tables) == {
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

    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(User)) > 0
        assert session.scalar(select(func.count()).select_from(Vehicle)) > 0
        assert session.scalar(select(func.count()).select_from(Order)) > 0
        assert session.scalar(select(func.count()).select_from(OrderItem)) > 0


def test_business_model_supports_storefront_fields():
    business = Business(
        id=1,
        owner_id=1,
        name="Freetown Auto Parts",
        slug="freetown-auto-parts",
        business_type="store",
        status="verified",
        city="Freetown",
        country="Sierra Leone",
        whatsapp="23276123456",
        email="sales@example.com",
        logo_url="https://example.com/logo.png",
        description="Vehicle parts and service",
    )

    public_data = _public(business)

    assert public_data["whatsapp"] == "23276123456"
    assert "email" not in public_data
    assert public_data["logo_url"] == "https://example.com/logo.png"
    assert public_data["type_label"] == "Local parts store"


def test_garage_car_accepts_valid_vin_and_rejects_invalid_vin():
    car = GarageCar(make="Honda", model="Accord", year=2003, vin="1hgcm82633a004352")
    assert car.vin == "1HGCM82633A004352"

    with pytest.raises(ValidationError):
        GarageCar(make="Honda", model="Accord", year=2003, vin="INVALID")
