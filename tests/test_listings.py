"""Car listings keep the seller's contact, mileage, fuel and gearbox."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import models
from app.db import sync_engine
from app.main import app


@pytest.fixture(scope="module")
def client():
    # These marketplace tables aren't in the test schema by default.
    for model in (models.Business, models.VehicleListing, models.PartListing):
        model.__table__.create(bind=sync_engine, checkfirst=True)
    with TestClient(app) as c:
        yield c


def _token(client):
    n = uuid.uuid4().hex[:8]
    r = client.post("/api/auth/register", json={
        "full_name": "Test Seller", "phone": "+2327" + str(int(n, 16))[:7], "email": f"{n}@example.com",
        "password": "password123", "account_type": "seller",
    })
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def test_car_listing_keeps_contact_and_specs(client):
    token = _token(client)
    r = client.post("/api/vehicles/", headers={"Authorization": f"Bearer {token}"}, json={
        "title": "2012 Toyota RAV4", "make": "Toyota", "model": "RAV4", "year": 2012,
        "price_sll": 185000000, "contact_phone": "+23276123456", "mileage_km": 120000,
        "fuel_type": "Petrol", "transmission": "Automatic", "location": "Freetown",
    })
    assert r.status_code == 201, r.text
    car = client.get(f"/api/vehicles/{r.json()['id']}").json()
    assert car["contact_phone"] == "+23276123456"
    assert car["mileage_km"] == 120000
    assert car["fuel_type"] == "Petrol" and car["transmission"] == "Automatic"
    assert "image_url" in car


def test_registration_saves_account_type(client):
    token = _token(client)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["roles"] == "seller"


def test_part_listing_can_be_created(client):
    token = _token(client)
    r = client.post("/api/parts/", headers={"Authorization": f"Bearer {token}"}, json={
        "name": "Front brake pads", "category": "Brakes", "price_sll": 185000,
        "compatible_make": "Toyota", "compatible_model": "Corolla", "location": "Freetown",
        "contact_phone": "+23276123456", "condition": "new", "stock_quantity": 3,
    })
    assert r.status_code == 201, r.text
    part = client.get(f"/api/parts/{r.json()['id']}").json()
    assert part["name"] == "Front brake pads" and part["stock_quantity"] == 3
