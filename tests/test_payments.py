"""Payment portal: mobile money references checked by an admin, and Monime online checkout."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import models, payments
from app.config import get_settings
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
                                                "email": f"p{n}@example.com", "password": "password123"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _order(client):
    seller, buyer = _user(client, "Mariama Kamara"), _user(client, "Abu Bangura")
    client.post("/api/businesses/apply", headers=seller, json={
        "business_type": "store", "name": f"Wellington Spares {uuid.uuid4().hex[:4]}", "city": "Freetown", "whatsapp": "076 555 999"})
    part = client.post("/api/parts/", headers=seller, json={"name": "Brake pads", "category": "Brakes", "price_sll": 100000, "stock_quantity": 5}).json()
    order = client.post("/api/shop/orders", headers=buyer, json={
        "items": [{"part_id": part["id"], "qty": 1}], "delivery_address": "4 Wilkinson Road",
        "delivery_area": "Freetown", "phone": "+23276333444"}).json()
    return seller, buyer, order


def _ref():
    return "PP" + uuid.uuid4().hex[:10].upper()


def test_pay_page_and_manual_flow(client):
    seller, buyer, order = _order(client)
    oid = order["id"]
    page = client.get(f"/api/pay/orders/{oid}", headers=buyer).json()
    assert page["amount_sll"] == 125000 and page["code"] == order["code"]
    assert [m["id"] for m in page["methods"]] == ["orange_money"]
    assert page["methods"][0]["number"] == get_settings().ORANGE_MONEY_NUMBER

    # Others can't see or pay it
    assert client.get(f"/api/pay/orders/{oid}", headers=seller).status_code == 404
    assert client.get(f"/api/pay/orders/{oid}").status_code == 401

    ref = _ref()
    r = client.post(f"/api/pay/orders/{oid}/manual", headers=buyer, json={"method": "orange_money", "reference": ref, "payer_phone": "076333444"})
    assert r.status_code == 201
    # one open check at a time
    assert client.post(f"/api/pay/orders/{oid}/manual", headers=buyer, json={"method": "orange_money", "reference": _ref(), "payer_phone": "076333444"}).status_code == 409

    todo = client.get("/api/admin/todo", headers=ADMIN).json()
    assert todo["order_payments_to_check"] >= 1
    listed = client.get("/api/shop/admin/orders?status=awaiting_payment", headers=ADMIN).json()["orders"]
    mine = [o for o in listed if o["id"] == oid][0]
    assert mine["payment"]["reference"] == ref and mine["payment"]["status"] == "submitted"

    # Admin: not found -> buyer sees the reason and tries again
    pid = mine["payment"]["id"]
    assert client.post(f"/api/pay/admin/payments/{pid}/reject", headers=ADMIN, json={"reason": "No payment with this ID"}).status_code == 200
    page = client.get(f"/api/pay/orders/{oid}", headers=buyer).json()
    assert page["payments"][0]["status"] == "rejected" and page["payments"][0]["note"] == "No payment with this ID"
    assert not page["waiting_for_check"]

    ref2 = _ref()
    pid2 = client.post(f"/api/pay/orders/{oid}/manual", headers=buyer, json={"method": "orange_money", "reference": ref2.lower(), "payer_phone": "076333444"}).json()["id"]
    assert client.post(f"/api/pay/admin/payments/{pid2}/approve", headers=ADMIN).status_code == 200
    assert client.post(f"/api/pay/admin/payments/{pid2}/approve", headers=ADMIN).status_code == 409
    assert client.get(f"/api/shop/orders/{oid}", headers=buyer).json()["status"] == "confirmed"
    rec = client.get(f"/api/shop/orders/{oid}/receipt", headers=buyer).json()
    assert rec["paid"] and rec["paid_with"]["method"] == "Orange Money" and rec["paid_with"]["reference"] == ref2.lower()

    # Paid orders can't be paid again
    assert client.post(f"/api/pay/orders/{oid}/manual", headers=buyer, json={"method": "orange_money", "reference": _ref(), "payer_phone": "076333444"}).status_code == 409


def test_reference_cannot_be_reused(client):
    _, buyer, order = _order(client)
    _, buyer2, order2 = _order(client)
    ref = _ref()
    assert client.post(f"/api/pay/orders/{order['id']}/manual", headers=buyer, json={"method": "orange_money", "reference": ref, "payer_phone": "076333444"}).status_code == 201
    # same ID with different spacing/case on another order
    spaced = ref[:4].lower() + " " + ref[4:]
    r = client.post(f"/api/pay/orders/{order2['id']}/manual", headers=buyer2, json={"method": "orange_money", "reference": spaced, "payer_phone": "076333444"})
    assert r.status_code == 409


def test_method_must_be_switched_on(client):
    _, buyer, order = _order(client)
    r = client.post(f"/api/pay/orders/{order['id']}/manual", headers=buyer, json={"method": "afrimoney", "reference": _ref(), "payer_phone": "076333444"})
    assert r.status_code == 400
    assert client.post(f"/api/pay/orders/{order['id']}/online", headers=buyer).status_code == 404


def test_old_confirm_button_settles_open_submission(client):
    _, buyer, order = _order(client)
    oid = order["id"]
    client.post(f"/api/pay/orders/{oid}/manual", headers=buyer, json={"method": "orange_money", "reference": _ref(), "payer_phone": "076333444"})
    assert client.post(f"/api/shop/admin/orders/{oid}/confirm-payment", headers=ADMIN).status_code == 200
    page = client.get(f"/api/pay/orders/{oid}", headers=buyer).json()
    assert page["status"] == "confirmed" and page["payments"][0]["status"] == "approved"


@pytest.fixture
def monime(monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "MONIME_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(cfg, "MONIME_SPACE_ID", "spc-test")
    state = {"sessions": {}, "created": []}

    async def create(payload, key):
        sid = "sps-" + uuid.uuid4().hex[:12]
        state["created"].append(payload)
        state["sessions"][sid] = {"id": sid, "status": "pending", "lineItems": payload["lineItems"]}
        return {"id": sid, "redirectUrl": "https://checkout.example/" + sid}

    async def get(sid):
        return state["sessions"].get(sid)

    monkeypatch.setattr(payments, "monime_create_session", create)
    monkeypatch.setattr(payments, "monime_get_session", get)
    monkeypatch.setattr(payments, "CHECK_GAP_SECONDS", 0)
    return state


def test_online_payment_confirmed_by_webhook(client, monime):
    _, buyer, order = _order(client)
    oid = order["id"]
    assert client.get(f"/api/pay/orders/{oid}", headers=buyer).json()["methods"][0]["id"] == "monime"
    r = client.post(f"/api/pay/orders/{oid}/online", headers=buyer).json()
    assert r["redirect_url"].startswith("https://checkout.example/sps-")
    payload = monime["created"][-1]
    assert payload["reference"] == order["code"]
    assert payload["lineItems"][0]["price"] == {"currency": "SLE", "value": 12500}  # SLL 125,000 = SLE 125.00
    sid = r["redirect_url"].rsplit("/", 1)[1]

    # A webhook claiming success is not believed while Monime still says pending
    client.post("/api/pay/monime/webhook", json={"event": "checkout_session.completed", "data": {"id": sid, "status": "completed"}})
    assert client.get(f"/api/shop/orders/{oid}", headers=buyer).json()["status"] == "awaiting_payment"

    monime["sessions"][sid]["status"] = "completed"
    client.post("/api/pay/monime/webhook", json={"event": "checkout_session.completed", "data": {"id": sid}})
    assert client.get(f"/api/shop/orders/{oid}", headers=buyer).json()["status"] == "confirmed"
    rec = client.get(f"/api/shop/orders/{oid}/receipt", headers=buyer).json()
    assert rec["paid_with"]["method"] == "Online (Monime)"


def test_online_payment_check_on_return_and_short_amount(client, monime):
    _, buyer, order = _order(client)
    oid = order["id"]
    sid = client.post(f"/api/pay/orders/{oid}/online", headers=buyer).json()["redirect_url"].rsplit("/", 1)[1]
    assert client.post(f"/api/pay/orders/{oid}/check", headers=buyer).json()["paid"] is False
    # Monime reports completed but for less money: goes to an admin instead
    monime["sessions"][sid].update(status="completed", lineItems=[{"price": {"currency": "SLE", "value": 100}}])
    assert client.post(f"/api/pay/orders/{oid}/check", headers=buyer).json()["paid"] is False
    page = client.get(f"/api/pay/orders/{oid}", headers=buyer).json()
    assert page["status"] == "awaiting_payment" and page["payments"][0]["status"] == "submitted"

    _, buyer2, order2 = _order(client)
    sid2 = client.post(f"/api/pay/orders/{order2['id']}/online", headers=buyer2).json()["redirect_url"].rsplit("/", 1)[1]
    monime["sessions"][sid2]["status"] = "completed"
    assert client.post(f"/api/pay/orders/{order2['id']}/check", headers=buyer2).json()["paid"] is True


def test_pay_page_served(client):
    r = client.get("/pay/1")
    assert r.status_code == 200 and "Pay for your order" in r.text
