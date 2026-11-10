"""Customer receipts and store copies for parts orders."""
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
        c.put("/api/shop/admin/settings", headers=ADMIN, json={"okada_fee_sll": 25000, "van_fee_sll": 80000, "platform_pct": 10})
        yield c


def _user(client, name):
    n = uuid.uuid4().hex[:8]
    r = client.post("/api/auth/register", json={"full_name": name, "phone": "+2327" + str(int(n, 16))[:7],
                                                "email": f"r{n}@example.com", "password": "password123"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _order(client):
    seller, buyer = _user(client, "Fatmata Sesay"), _user(client, "Ibrahim Koroma")
    slug = client.post("/api/businesses/apply", headers=seller, json={
        "business_type": "store", "name": f"Kissy Spares {uuid.uuid4().hex[:4]}", "city": "Freetown", "whatsapp": "076 555 123"}).json()["slug"]
    part = client.post("/api/parts/", headers=seller, json={"name": "Brake pads", "category": "Brakes", "price_sll": 100000, "stock_quantity": 5}).json()
    filt = client.post("/api/parts/", headers=seller, json={"name": "Oil filter", "category": "Filters", "price_sll": 45000, "stock_quantity": 5}).json()
    order = client.post("/api/shop/orders", headers=buyer, json={
        "items": [{"part_id": part["id"], "qty": 2}, {"part_id": filt["id"], "qty": 1}],
        "delivery_address": "12 Kissy Road", "delivery_area": "Freetown", "phone": "+23276111222"}).json()
    return seller, buyer, order, slug


def test_customer_receipt_before_and_after_payment(client):
    seller, buyer, order, _ = _order(client)
    r = client.get(f"/api/shop/orders/{order['id']}/receipt", headers=buyer).json()
    assert r["title"] == "Order summary" and r["paid"] is False
    assert r["number"] == order["code"] and r["total_sll"] == 270000
    assert r["parts_total_sll"] == 245000 and r["delivery_fee_sll"] == 25000
    assert [(i["title"], i["qty"], i["line_total_sll"]) for i in r["items"]] == [("Brake pads", 2, 200000), ("Oil filter", 1, 45000)]
    assert r["seller"]["name"].startswith("Kissy Spares") and r["seller"]["phone"].endswith("76555123")
    assert r["buyer"]["address"] == "12 Kissy Road"
    assert "platform_fee_sll" not in r and "driver_payout_sll" not in r and "delivery_code" not in str(r)

    client.post(f"/api/shop/admin/orders/{order['id']}/confirm-payment", headers=ADMIN)
    r = client.get(f"/api/shop/orders/{order['id']}/receipt", headers=buyer).json()
    assert r["title"] == "Receipt" and r["paid"] is True and r["paid_at"]


def test_store_copy(client):
    seller, buyer, order, _ = _order(client)
    client.post(f"/api/shop/admin/orders/{order['id']}/confirm-payment", headers=ADMIN)
    r = client.get(f"/api/shop/orders/{order['id']}/receipt?copy=store", headers=seller).json()
    assert r["title"] == "Sales slip" and r["amount_due_to_store_sll"] == 245000
    assert "delivery_fee_sll" not in r and "total_sll" not in r
    assert r["buyer"]["address"] is None and r["buyer"]["area"] == "Freetown"


def test_who_can_see_which_copy(client):
    seller, buyer, order, _ = _order(client)
    stranger = _user(client, "Someone Else")
    oid = order["id"]
    assert client.get(f"/api/shop/orders/{oid}/receipt?copy=store", headers=buyer).status_code == 404
    assert client.get(f"/api/shop/orders/{oid}/receipt", headers=seller).status_code == 404
    assert client.get(f"/api/shop/orders/{oid}/receipt", headers=stranger).status_code == 404
    assert client.get(f"/api/shop/orders/{oid}/receipt").status_code == 401
    assert client.get(f"/api/shop/orders/{oid}/receipt?copy=store", headers=ADMIN).status_code == 200
    assert client.get(f"/api/shop/orders/{oid}/receipt", headers=ADMIN).status_code == 200
    assert client.get("/api/shop/orders/999999/receipt", headers=ADMIN).status_code == 404


def test_cancelled_order(client):
    seller, buyer, order, _ = _order(client)
    client.post(f"/api/shop/orders/{order['id']}/cancel", headers=buyer)
    store = client.get(f"/api/shop/orders/{order['id']}/receipt?copy=store", headers=seller).json()
    assert store["status"] == "cancelled" and store["amount_due_to_store_sll"] == 0
    cust = client.get(f"/api/shop/orders/{order['id']}/receipt", headers=buyer).json()
    assert cust["paid"] is False and cust["cancelled_at"]


def test_receipt_page(client):
    assert client.get("/receipt?id=1").status_code == 200
