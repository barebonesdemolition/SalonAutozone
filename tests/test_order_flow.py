"""The whole parts order, from checkout to delivery, using the admin key where an admin acts."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import models
from app.db import sync_engine
from app.main import app

ADMIN = {"X-Admin-Key": "test-admin-secret"}


@pytest.fixture(scope="module")
def client():
    for model in (models.Business, models.VehicleListing, models.PartListing):
        model.__table__.create(bind=sync_engine, checkfirst=True)
    with TestClient(app) as c:
        yield c


def _user(client, kind="buyer"):
    n = uuid.uuid4().hex[:8]
    r = client.post("/api/auth/register", json={
        "full_name": f"{kind.title()} Person", "phone": "+2327" + str(int(n, 16))[:7], "email": f"{kind}{n}@example.com",
        "password": "password123", "account_type": "seller" if kind == "seller" else "buyer"})
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_order_from_checkout_to_delivery(client):
    # Admin sets delivery fees (with the admin key, not an admin login).
    r = client.put("/api/shop/admin/settings", headers=ADMIN, json={"okada_fee_sll": 25000, "van_fee_sll": 80000, "platform_pct": 10})
    assert r.status_code == 200, r.text

    seller, buyer, driver = _user(client, "seller"), _user(client), _user(client, "driver")
    part = client.post("/api/parts/", headers=seller, json={"name": "Brake pads", "category": "Brakes", "price_sll": 100000, "stock_quantity": 5}).json()

    order = client.post("/api/shop/orders", headers=buyer, json={
        "items": [{"part_id": part["id"], "qty": 2}], "delivery_address": "12 Kissy Road", "delivery_area": "Freetown", "phone": "+23276111222"})
    assert order.status_code == 201, order.text
    order = order.json()
    assert order["total_to_pay_sll"] == 225000

    # The seller sees it, but can't mark it ready before payment.
    sales = client.get("/api/shop/seller/orders", headers=seller).json()["orders"]
    assert [o["id"] for o in sales] == [order["id"]]
    assert client.post(f"/api/shop/seller/orders/{order['id']}/ready", headers=seller).status_code == 409

    assert client.get("/api/shop/admin/orders", headers=ADMIN).json()["count"] >= 1
    assert client.post(f"/api/shop/admin/orders/{order['id']}/confirm-payment", headers=ADMIN).status_code == 200
    assert client.post(f"/api/shop/seller/orders/{order['id']}/ready", headers=seller).status_code == 200

    # A driver applies, the admin approves, the driver delivers with the buyer's code.
    assert client.post("/api/drivers/apply", headers=driver, json={"vehicle_type": "bike", "city": "Freetown", "phone": "+23277000111"}).status_code == 201
    pending = client.get("/api/drivers/admin/list?status=pending", headers=ADMIN).json()["drivers"]
    driver_id = pending[0]["id"]
    assert client.post(f"/api/drivers/admin/{driver_id}/status?status=verified", headers=ADMIN).status_code == 200
    assert client.post("/api/drivers/me/online?online=true", headers=driver).status_code == 200

    job = client.get("/api/shop/driver/jobs?scope=open", headers=driver).json()["jobs"][0]
    assert client.post(f"/api/shop/driver/jobs/{job['id']}/accept", headers=driver).status_code == 200
    assert client.post(f"/api/shop/driver/jobs/{job['id']}/pickup", headers=driver).status_code == 200
    assert client.get(f"/api/shop/orders/{order['id']}", headers=buyer).json()["status"] == "out_for_delivery"
    assert client.post(f"/api/shop/driver/jobs/{job['id']}/deliver", headers=driver, json={"code": "000000" if order["delivery_code"] != "000000" else "111111"}).status_code == 400
    assert client.post(f"/api/shop/driver/jobs/{job['id']}/deliver", headers=driver, json={"code": order["delivery_code"]}).status_code == 200
    assert client.get(f"/api/shop/orders/{order['id']}", headers=buyer).json()["status"] == "delivered"


def test_admin_endpoints_reject_strangers(client):
    buyer = _user(client)
    assert client.get("/api/shop/admin/orders", headers=buyer).status_code == 403
    assert client.get("/api/drivers/admin/list", headers=buyer).status_code == 403


@pytest.mark.parametrize("name,category,size", [
    ("Spark plugs set", "Engine", "small"),
    ("Engine oil 5W-30, 4L", "Fluids", "small"),
    ("ATF transmission fluid", "Transmission", "small"),
    ("Complete engine 1ZZ-FE", "Engine", "bulky"),
    ("Front bumper, Corolla", "Body", "bulky"),
    ("Gearbox, automatic", "Transmission", "bulky"),
])
def test_delivery_size_from_part_name(name, category, size):
    from types import SimpleNamespace
    from app.shop import _default_size
    assert _default_size(SimpleNamespace(name=name, category=category)) == size
