"""Alerts at each order step, and refunds."""
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
                                                "email": f"a{n}@example.com", "password": "password123"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _order(client):
    seller, buyer = _user(client, "Alert Shop"), _user(client, "Alert Buyer")
    client.post("/api/businesses/apply", headers=seller, json={"business_type": "store", "name": f"Alert Spares {uuid.uuid4().hex[:4]}", "city": "Freetown", "whatsapp": "076 222 333"})
    part = client.post("/api/parts/", headers=seller, json={"name": "Fan belt", "category": "Engine", "price_sll": 30000, "stock_quantity": 4}).json()
    order = client.post("/api/shop/orders", headers=buyer, json={"items": [{"part_id": part["id"], "qty": 1}],
                        "delivery_address": "1 Main Road", "delivery_area": "Freetown", "phone": "+23276444555"}).json()
    return seller, buyer, order, part


def _titles(client, who):
    return [a["title"] for a in client.get("/api/alerts", headers=who).json()["alerts"]]


def test_alerts_follow_the_order(client):
    seller, buyer, order, _ = _order(client)
    assert any(t.startswith("New order " + order["code"]) for t in _titles(client, seller))
    assert client.get("/api/alerts/unread", headers=seller).json()["unread"] >= 1
    client.post(f"/api/shop/admin/orders/{order['id']}/confirm-payment", headers=ADMIN)
    assert f"Payment received for {order['code']}" in _titles(client, buyer)
    assert f"Order {order['code']} is paid" in _titles(client, seller)
    client.post(f"/api/shop/seller/orders/{order['id']}/ready", headers=seller)
    assert f"Your parts are ready ({order['code']})" in _titles(client, buyer)
    # reading clears the badge
    client.post("/api/alerts/read", headers=buyer)
    assert client.get("/api/alerts/unread", headers=buyer).json()["unread"] == 0
    # nobody else sees them
    assert client.get("/api/alerts", headers=_user(client, "Stranger")).json()["alerts"] == []
    # undo: leave no open job for other tests' riders
    from sqlalchemy import text
    with sync_engine.begin() as c:
        c.execute(text("UPDATE delivery_jobs SET status='cancelled' WHERE order_id=:i"), {"i": order["id"]})


def test_push_subscribe_and_key(client, monkeypatch):
    import app.notify as n
    sent = []
    monkeypatch.setattr(n, "_send_one", lambda sub, payload, pem: sent.append(payload) or True)
    buyer = _user(client, "Phone Person")
    key = client.get("/api/push/key").json()["key"]
    assert len(key) > 60 and client.get("/api/push/key").json()["key"] == key  # stable
    sub = {"endpoint": "https://push.example.com/abc" + uuid.uuid4().hex, "keys": {"p256dh": "B" * 40, "auth": "a" * 16}}
    assert client.post("/api/push/subscribe", headers=buyer, json=sub).status_code == 200
    assert client.post("/api/push/subscribe", headers=buyer, json=sub).status_code == 200  # same phone twice is fine
    assert client.get("/api/alerts", headers=buyer).json()["phones"] == 1
    assert client.post("/api/push/subscribe", headers=buyer, json={**sub, "endpoint": "http://insecure.example.com/xxxxxxxxxxxxxxxx"}).status_code == 400
    assert client.post("/api/push/unsubscribe", headers=buyer, json={"endpoint": sub["endpoint"]}).status_code == 200
    assert client.get("/api/alerts", headers=buyer).json()["phones"] == 0


def test_buyer_cancels_paid_order_gets_refund(client):
    seller, buyer, order, part = _order(client)
    oid = order["id"]
    client.post(f"/api/shop/admin/orders/{oid}/confirm-payment", headers=ADMIN)
    r = client.post(f"/api/shop/orders/{oid}/cancel", headers=buyer).json()
    assert r["refund"] is True
    refs = client.get(f"/api/refunds/orders/{oid}", headers=buyer).json()["refunds"]
    assert refs[0]["status"] == "to_send" and refs[0]["amount_sll"] == 55000 and refs[0]["refund_phone"] == "+23276444555"
    assert client.get("/api/admin/todo", headers=ADMIN).json()["refunds_to_send"] >= 1
    assert client.post(f"/api/refunds/admin/{refs[0]['id']}/sent", headers=ADMIN, json={"reference": "RF1"}).status_code == 200
    assert client.post(f"/api/refunds/admin/{refs[0]['id']}/sent", headers=ADMIN, json={}).status_code == 409
    assert client.get(f"/api/refunds/orders/{oid}", headers=buyer).json()["refunds"][0]["status"] == "sent"
    assert f"Refund sent for {order['code']}" in _titles(client, buyer)
    assert f"Order {order['code']} was cancelled" in _titles(client, seller)


def test_unpaid_cancel_has_no_refund(client):
    _, buyer, order, _ = _order(client)
    assert client.post(f"/api/shop/orders/{order['id']}/cancel", headers=buyer).json()["refund"] is False
    assert client.get(f"/api/refunds/orders/{order['id']}", headers=buyer).json()["refunds"] == []


def test_problem_after_delivery(client):
    seller, buyer, order, _ = _order(client)
    oid = order["id"]
    assert client.get(f"/api/refunds/orders/{oid}", headers=buyer).json()["can_report_problem"] is False
    client.post(f"/api/shop/admin/orders/{oid}/confirm-payment", headers=ADMIN)
    from sqlalchemy import text
    with sync_engine.begin() as c:
        c.execute(text("UPDATE shop_orders SET status='delivered' WHERE id=:i"), {"i": oid})
        c.execute(text("INSERT INTO shop_order_events (order_id, status, note, created_at) VALUES (:i, 'delivered', 'test', CURRENT_TIMESTAMP)"), {"i": oid})
    assert client.post(f"/api/refunds/orders/{oid}/problem", headers=buyer, json={"reason": "short"}).status_code == 422
    r = client.post(f"/api/refunds/orders/{oid}/problem", headers=buyer, json={"reason": "The belt is the wrong size for my car"})
    assert r.status_code == 201
    assert client.post(f"/api/refunds/orders/{oid}/problem", headers=buyer, json={"reason": "Asking again for the same thing"}).status_code == 409
    rid = r.json()["id"]
    assert client.post(f"/api/refunds/admin/{rid}/sent", headers=ADMIN, json={}).status_code == 409  # not approved yet
    assert client.post(f"/api/refunds/admin/{rid}/approve", headers=ADMIN, json={"amount_sll": 999999}).status_code == 400
    assert client.post(f"/api/refunds/admin/{rid}/approve", headers=ADMIN, json={"amount_sll": 30000}).status_code == 200
    # the shop payout list shows it so the shop is paid less
    mine = [p for p in client.get("/api/shop/admin/shop-payouts", headers=ADMIN).json()["payouts"] if p["order_id"] == oid][0]
    assert mine["refunded_sll"] == 30000


def test_admin_cancel_and_refund_before_pickup(client):
    seller, buyer, order, part = _order(client)
    oid = order["id"]
    assert client.post(f"/api/refunds/admin/orders/{oid}", headers=ADMIN, json={"reason": "Not paid yet"}).status_code == 409
    client.post(f"/api/shop/admin/orders/{oid}/confirm-payment", headers=ADMIN)
    client.post(f"/api/shop/seller/orders/{oid}/ready", headers=seller)
    r = client.post(f"/api/refunds/admin/orders/{oid}", headers=ADMIN, json={"reason": "Shop ran out of stock"})
    assert r.status_code == 200 and r.json()["order_status"] == "cancelled"
    from sqlalchemy import text
    with sync_engine.begin() as c:
        assert c.execute(text("SELECT status FROM delivery_jobs WHERE order_id=:i"), {"i": oid}).scalar() == "cancelled"
        assert c.execute(text("SELECT stock_quantity FROM part_listings WHERE id=:i"), {"i": part["id"]}).scalar() == 4
    assert client.post(f"/api/refunds/admin/orders/{oid}", headers=ADMIN, json={"reason": "Again"}).status_code == 409
    assert client.get("/api/refunds/admin?status=open", headers=ADMIN).json()["count"] >= 1


def test_alerts_page_and_worker_served(client):
    assert client.get("/alerts").status_code == 200
    r = client.get("/sw.js")
    assert r.status_code == 200 and "showNotification" in r.text
