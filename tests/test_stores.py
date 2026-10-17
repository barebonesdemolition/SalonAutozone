"""Business page personalisation, the camera stock scanner and adding stock in bulk."""
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app import stores
from app.main import app

ADMIN = {"X-Admin-Key": "test-admin-secret"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _owner(client, verified=True):
    n = int(uuid.uuid4().hex[:6], 16) % 900000 + 100000
    reg = client.post("/api/auth/register", json={"full_name": "Sorie Kanu", "phone": f"+232 78 {n}",
                                                  "email": f"s{n}{uuid.uuid4().hex[:4]}@example.com", "password": "password123",
                                                  "account_type": "business"}).json()
    auth = {"Authorization": "Bearer " + reg["access_token"]}
    r = client.post("/api/businesses/apply", headers=auth, json={
        "business_type": "store", "name": f"Kanu Spares {n}", "city": "Bo", "whatsapp": f"078{n}"})
    assert r.status_code == 201, r.text
    slug = r.json()["slug"]
    if verified:
        bid = client.get("/api/businesses/mine", headers=auth).json()["business"]["id"]
        client.post(f"/api/businesses/admin/{bid}/status?status=verified", headers=ADMIN)
    return auth, slug


def _jpeg():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 50, 50)).save(buf, "JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- profile

def test_personalise_page(client):
    auth, slug = _owner(client)
    r = client.put("/api/businesses/mine/profile", headers=auth, json={
        "tagline": "  Genuine Toyota parts since 2009 ", "accent": "red", "address": "12 Kissy Road",
        "hours": {"mon": "08:00-18:00", "sat": "09:00-14:00", "sun": "closed"},
        "call_phone": "076 555 123", "facebook": "@kanuspares", "instagram": "https://www.instagram.com/kanu",
        "tiktok": "kanu.spares", "specialties": ["Toyota", "Nissan", "Toyota", "<b>x</b>"],
        "cover_url": "https://res.cloudinary.com/demo/image/upload/v1/cover.jpg"})
    assert r.status_code == 200, r.text
    page = client.get(f"/api/businesses/{slug}").json()
    assert page["tagline"] == "Genuine Toyota parts since 2009"
    assert page["accent"] == "red" and page["accent_hex"] == stores.ACCENTS["red"]
    assert page["hours"]["mon"] == "08:00-18:00" and page["hours"]["sun"] == "closed"
    assert page["facebook"] == "https://www.facebook.com/kanuspares"
    assert page["tiktok"] == "https://www.tiktok.com/@kanu.spares"
    assert page["specialties"] == ["Toyota", "Nissan", "bxb"]  # tags stripped to plain text
    assert page["status"] == "verified"  # personalising doesn't send the business back for checking

    html = client.get(f"/store/{slug}").text
    assert "Genuine Toyota parts since 2009" in html
    assert "w_1200,h_630,c_fill" in html  # cover used for WhatsApp previews

    # Clearing works too.
    client.put("/api/businesses/mine/profile", headers=auth, json={"tagline": "", "hours": None, "cover_url": None})
    page = client.get(f"/api/businesses/{slug}").json()
    assert page["tagline"] is None and page["hours"] is None and page["cover_url"] is None


@pytest.mark.parametrize("body", [
    {"accent": "pink"}, {"hours": {"mon": "8am-6pm"}}, {"hours": {"funday": "08:00-18:00"}},
    {"cover_url": "javascript:alert(1)"}, {"facebook": "https://evil.example/kanu"}, {"call_phone": "123"},
    {"tagline": "x" * 91},
])
def test_profile_rejects_bad_values(client, body):
    auth, _ = _owner(client, verified=False)
    assert client.put("/api/businesses/mine/profile", headers=auth, json=body).status_code == 422


def test_profile_needs_a_business(client):
    n = uuid.uuid4().hex[:6]
    tok = client.post("/api/auth/register", json={"full_name": "No Biz", "phone": f"+232 79 {int(n, 16) % 900000 + 100000}",
                                                  "email": f"nb{n}@example.com", "password": "password123"}).json()["access_token"]
    auth = {"Authorization": "Bearer " + tok}
    assert client.put("/api/businesses/mine/profile", headers=auth, json={"tagline": "hi"}).status_code == 404
    assert client.post("/api/stock/bulk", headers=auth, json={"items": [{"name": "Oil filter", "category": "Filters", "price_sll": 5}]}).status_code == 404


# ---------------------------------------------------------------- scanner

def test_clean_items_keeps_only_good_data():
    raw = {"items": [
        {"name": "Front brake pads", "category": "brakes", "brand": "Akebono", "part_number": "null",
         "compatible_make": "Toyota", "condition": "NEW", "quantity": "4", "confidence": "high", "box_2d": [100, 200, 400, 600]},
        {"name": "  ", "category": "Filters"},
        {"name": "Mystery thing", "category": "Spaceship", "quantity": -3, "box_2d": [10, 10, 12, 12], "condition": "broken"},
        "not a dict",
    ]}
    items = stores.clean_items(raw)
    assert len(items) == 2
    a, b = items
    assert a["category"] == "Brakes" and a["condition"] == "new" and a["quantity"] == 4
    assert a["part_number"] is None and a["box"] == [100, 200, 400, 600]
    assert b["category"] == "Accessories" and b["quantity"] == 1 and b["box"] is None and b["condition"] == "used"
    assert stores.clean_items({"items": [{"name": f"P{i}", "category": "Engine"} for i in range(80)]}).__len__() == stores.MAX_ITEMS_PER_SCAN


def test_scan_with_ai(client, monkeypatch):
    from app.config import get_settings
    auth, _ = _owner(client)
    monkeypatch.setattr(get_settings(), "GEMINI_API_KEY", "")
    off = client.post("/api/stock/scan", headers=auth, files={"file": ("s.jpg", _jpeg(), "image/jpeg")})
    assert off.status_code == 503

    monkeypatch.setattr(get_settings(), "GEMINI_API_KEY", "test")
    seen = {}

    def fake_ai(image, mime):
        seen["mime"], seen["size"] = mime, len(image)
        return {"items": [{"name": "Oil filter", "category": "Filters", "brand": "Denso", "quantity": 6,
                           "condition": "new", "confidence": "high", "box_2d": [0, 0, 500, 500]}]}

    monkeypatch.setattr(stores, "_ask_ai", fake_ai)
    r = client.post("/api/stock/scan", headers=auth, files={"file": ("s.jpg", _jpeg(), "image/jpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["name"] == "Oil filter" and r.json()["items"][0]["quantity"] == 6
    assert seen["mime"] == "image/jpeg" and seen["size"] > 0

    assert client.post("/api/stock/scan", headers=auth, files={"file": ("s.txt", b"hello", "text/plain")}).status_code == 400
    assert client.post("/api/stock/scan", files={"file": ("s.jpg", _jpeg(), "image/jpeg")}).status_code == 401

    def broken(image, mime):
        raise RuntimeError("model down")

    monkeypatch.setattr(stores, "_ask_ai", broken)
    down = client.post("/api/stock/scan", headers=auth, files={"file": ("s.jpg", _jpeg(), "image/jpeg")})
    assert down.status_code == 502 and "model" not in down.json()["detail"]


def test_scan_daily_limit(client, monkeypatch):
    from app.config import get_settings
    auth, _ = _owner(client)
    monkeypatch.setattr(get_settings(), "GEMINI_API_KEY", "test")
    monkeypatch.setattr(stores, "_ask_ai", lambda image, mime: {"items": []})
    monkeypatch.setattr(stores, "SCANS_PER_DAY", 2)
    for _ in range(2):
        assert client.post("/api/stock/scan", headers=auth, files={"file": ("s.jpg", _jpeg(), "image/jpeg")}).status_code == 200
    assert client.post("/api/stock/scan", headers=auth, files={"file": ("s.jpg", _jpeg(), "image/jpeg")}).status_code == 429


# ---------------------------------------------------------------- bulk add

def test_bulk_add_then_photos_and_store_page(client):
    auth, slug = _owner(client)
    client.put("/api/businesses/mine/profile", headers=auth, json={"call_phone": "076 555 123"})
    r = client.post("/api/stock/bulk", headers=auth, json={"items": [
        {"name": "Front brake pads", "category": "Brakes", "price_sll": 185000, "stock_quantity": 4, "condition": "new",
         "brand": "Akebono", "part_number": "04465-02220", "compatible_make": "Toyota", "compatible_model": "Corolla"},
        {"name": "Denso oil filter", "category": "Filters", "price_sll": 45000, "brand": "Denso"},
    ]})
    assert r.status_code == 201, r.text
    created = r.json()["created"]
    assert [c["name"] for c in created] == ["Akebono Front brake pads", "Denso oil filter"]

    part = client.get(f"/api/parts/{created[0]['id']}").json()
    assert part["location"] == "Bo" and part["contact_phone"] == "076 555 123"
    assert "Part number: 04465-02220" in part["description"] and part["stock_quantity"] == 4

    up = client.post(f"/api/photos/part/{created[0]['id']}", headers=auth, files={"file": ("p.jpg", _jpeg(), "image/jpeg")})
    assert up.status_code == 201, up.text

    store = client.get(f"/api/businesses/{slug}").json()
    names = {p["name"] for p in store["listings"]["parts"]}
    assert {"Akebono Front brake pads", "Denso oil filter"} <= names


@pytest.mark.parametrize("item", [
    {"name": "Oil filter", "category": "Filters", "price_sll": 0},
    {"name": "Oil filter", "category": "Spaceship", "price_sll": 100},
    {"name": "Oil filter", "category": "Filters", "price_sll": 100, "condition": "broken"},
    {"name": "O", "category": "Filters", "price_sll": 100},
])
def test_bulk_add_rejects_bad_items(client, item):
    auth, _ = _owner(client)
    assert client.post("/api/stock/bulk", headers=auth, json={"items": [item]}).status_code == 422


def test_bulk_add_limit(client):
    auth, _ = _owner(client)
    items = [{"name": f"Part {i}", "category": "Engine", "price_sll": 1000} for i in range(stores.MAX_BULK + 1)]
    assert client.post("/api/stock/bulk", headers=auth, json={"items": items}).status_code == 422


def test_pages_exist(client):
    for path in ("/business/stock", "/business/page"):
        assert client.get(path).status_code == 200
    assert client.get("/api/stores/options").json()["categories"][0] == "Fluids"
