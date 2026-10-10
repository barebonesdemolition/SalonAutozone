"""Shared part/car links get their own title, price and photo in WhatsApp previews."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import models
from app.db import sync_engine
from app.main import app


@pytest.fixture(scope="module")
def client():
    for model in (models.Business, models.VehicleListing, models.PartListing):
        model.__table__.create(bind=sync_engine, checkfirst=True)
    with TestClient(app) as c:
        yield c


def _auth(client):
    n = uuid.uuid4().hex[:8]
    r = client.post("/api/auth/register", json={"full_name": "Share Seller", "phone": "+2326" + str(int(n, 16))[:7],
                                                "email": f"s{n}@example.com", "password": "password123", "account_type": "seller"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_part_page_has_preview_tags(client):
    auth = _auth(client)
    pid = client.post("/api/parts/", headers=auth, json={
        "name": 'Brake pads "OEM" <front>', "category": "Brakes", "price_sll": 185000, "condition": "new",
        "compatible_make": "Toyota", "compatible_model": "Corolla", "location": "Freetown",
        "image_url": "https://res.cloudinary.com/demo/image/upload/v1/saloncarparts/abc.jpg"}).json()["id"]
    page = client.get(f"/part/{pid}", headers={"x-forwarded-proto": "https", "host": "saloncarparts.onrender.com"}).text
    assert '<meta property="og:title" content="Brake pads &quot;OEM&quot; &lt;front&gt;">' in page
    assert "SLL 185,000, New, fits Toyota Corolla, Freetown" in page
    assert 'content="https://res.cloudinary.com/demo/image/upload/w_1200,h_630,c_fill,f_jpg,q_auto/v1/saloncarparts/abc.jpg"' in page
    assert f'<meta property="og:url" content="https://saloncarparts.onrender.com/part/{pid}">' in page
    assert "<title>Brake pads &quot;OEM&quot; &lt;front&gt; · SalonAutoZone</title>" in page
    assert page.count('name="description"') == 1


def test_car_page_has_preview_tags_and_local_photo_is_absolute(client):
    auth = _auth(client)
    vid = client.post("/api/vehicles/", headers=auth, json={
        "title": "2012 Toyota RAV4", "make": "Toyota", "model": "RAV4", "year": 2012, "price_sll": 185000000,
        "mileage_km": 120000, "transmission": "Automatic", "location": "Bo"}).json()["id"]
    page = client.get(f"/vehicle/{vid}").text
    assert '<meta property="og:title" content="2012 Toyota RAV4">' in page
    assert "SLL 185,000,000, 2012, 120,000 km, Automatic, Bo" in page


def test_unknown_listing_still_serves_the_page(client):
    assert client.get("/part/999999").status_code == 200
    assert client.get("/vehicle/not-a-number").status_code == 200


def test_home_page_has_preview_tags(client):
    page = client.get("/").text
    assert 'property="og:title"' in page and 'property="og:image"' in page
