"""Riders are paid only once the buyer has the parts, by the shop or by SalonAutoZone."""
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
        c.put("/api/shop/admin/settings", headers=ADMIN, json={"okada_fee_sll": 25000, "van_fee_sll": 80000, "platform_pct": 10, "commission_pct": 0})
        yield c


def _user(client, name):
    n = uuid.uuid4().hex[:8]
    r = client.post("/api/auth/register", json={"full_name": name, "phone": "+2327" + str(int(n, 16))[:7],
                                                "email": f"d{n}@example.com", "password": "password123"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _rider(client):
    rider = _user(client, "Alusine Rider")
    phone = "+2327" + str(uuid.uuid4().int)[:7]
    assert client.post("/api/drivers/apply", headers=rider, json={"vehicle_type": "bike", "city": "Freetown", "phone": phone}).status_code == 201
    mine = [d for d in client.get("/api/drivers/admin/list?status=pending", headers=ADMIN).json()["drivers"] if d.get("phone") == phone][0]
    client.post(f"/api/drivers/admin/{mine['id']}/status?status=verified", headers=ADMIN)
    client.post("/api/drivers/me/online?online=true", headers=rider)
    return rider, phone


def _delivered(client, shop_paid):
    seller, buyer = _user(client, "Shop Owner"), _user(client, "Buyer")
    client.post("/api/businesses/apply", headers=seller, json={"business_type": "store", "name": f"Rider Test {uuid.uuid4().hex[:4]}", "city": "Freetown", "whatsapp": "076 111 333"})
    if shop_paid:
        client.put("/api/businesses/mine/payments", headers=seller, json={"pay_orange_money": "076111333", "okada_fee_sll": 20000, "van_fee_sll": 60000})
    part = client.post("/api/parts/", headers=seller, json={"name": "Oil filter", "category": "Filters", "price_sll": 40000, "stock_quantity": 3}).json()
    order = client.post("/api/shop/orders", headers=buyer, json={"items": [{"part_id": part["id"], "qty": 1}],
                        "delivery_address": "2 Hill Station", "delivery_area": "Freetown", "phone": "+23276000555"}).json()
    client.post(f"/api/shop/admin/orders/{order['id']}/confirm-payment", headers=ADMIN)
    client.post(f"/api/shop/seller/orders/{order['id']}/ready", headers=seller)
    rider, phone = _rider(client)
    job = [j for j in client.get("/api/shop/driver/jobs?scope=open", headers=rider).json()["jobs"] if j["code"] == order["code"]][0]
    assert client.post(f"/api/shop/driver/jobs/{job['id']}/accept", headers=rider).status_code == 200
    client.post(f"/api/shop/driver/jobs/{job['id']}/pickup", headers=rider)
    return seller, buyer, rider, phone, order, job


def test_platform_pays_rider_after_delivery(client):
    seller, buyer, rider, phone, order, job = _delivered(client, shop_paid=False)
    code = client.get(f"/api/shop/orders/{order['id']}", headers=buyer).json()["delivery_code"]
    client.post(f"/api/shop/driver/jobs/{job['id']}/deliver", headers=rider, json={"code": code})
    assert client.get("/api/admin/todo", headers=ADMIN).json()["riders_to_pay"] >= 1
    mine = [p for p in client.get("/api/shop/admin/rider-payouts", headers=ADMIN).json()["payouts"] if p["job_id"] == job["id"]][0]
    assert mine["paid_by"] == "platform" and mine["amount_sll"] == 22500
    # rider sees it waiting until SalonAutoZone pays
    e = client.get("/api/shop/driver/earnings", headers=rider).json()
    assert e["waiting_sll"] == 22500 and e["recent"][0]["paid_by"] == "SalonAutoZone"
    # the shop can't mark a SalonAutoZone-paid rider as paid
    assert client.post(f"/api/shop/seller/rider-payouts/{job['id']}/paid", headers=seller, json={}).status_code == 409
    assert client.post(f"/api/shop/admin/rider-payouts/{job['id']}/paid", headers=ADMIN, json={}).status_code == 200
    assert client.post(f"/api/shop/admin/rider-payouts/{job['id']}/paid", headers=ADMIN, json={}).status_code == 409
    assert client.get("/api/shop/driver/earnings", headers=rider).json()["waiting_sll"] == 0


def test_shop_with_own_fees_rider_paid_by_salonautozone(client):
    seller, buyer, rider, phone, order, job = _delivered(client, shop_paid=True)
    assert job["paid_by_shop"] is False
    code = client.get(f"/api/shop/orders/{order['id']}", headers=buyer).json()["delivery_code"]
    client.post(f"/api/shop/driver/jobs/{job['id']}/deliver", headers=rider, json={"code": code})
    mine = [p for p in client.get("/api/shop/admin/rider-payouts", headers=ADMIN).json()["payouts"] if p["job_id"] == job["id"]][0]
    assert mine["paid_by"] == "platform" and mine["amount_sll"] == 18000  # shop's 20,000 fee less 10%
    assert client.get("/api/shop/seller/rider-payouts", headers=seller).json()["count"] == 0
