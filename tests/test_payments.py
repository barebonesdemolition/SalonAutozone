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
    assert payload["lineItems"][0]["price"] == {"currency": "SLE", "value": 12500000}  # Le 125,000.00 in cents
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


def _shop_order(client, fees=True, number="076 570 111"):
    seller, buyer = _user(client, "Hawa Conteh"), _user(client, "Musa Kargbo")
    client.post("/api/businesses/apply", headers=seller, json={
        "business_type": "store", "name": f"Lumley Motors {uuid.uuid4().hex[:4]}", "city": "Freetown", "whatsapp": "076 570 111"})
    body = {"pay_orange_money": number}
    if fees:
        body.update(okada_fee_sll=15000, van_fee_sll=60000)
    r = client.put("/api/businesses/mine/payments", headers=seller, json=body)
    assert r.status_code == 200, r.text
    part = client.post("/api/parts/", headers=seller, json={"name": "Spark plugs", "category": "Engine", "price_sll": 50000, "stock_quantity": 9}).json()
    order = client.post("/api/shop/orders", headers=buyer, json={
        "items": [{"part_id": part["id"], "qty": 2}], "delivery_address": "8 Lumley Beach Road",
        "delivery_area": "Freetown", "phone": "+23276888999"}).json()
    return seller, buyer, order, r.json()


def test_shop_sets_numbers_and_fees(client):
    seller = _user(client, "Sia Koroma")
    assert client.get("/api/businesses/mine/payments", headers=seller).status_code == 404
    client.post("/api/businesses/apply", headers=seller, json={"business_type": "store", "name": f"Bo Spares {uuid.uuid4().hex[:4]}", "city": "Bo", "whatsapp": "077 111 222"})
    r = client.put("/api/businesses/mine/payments", headers=seller, json={"pay_orange_money": "076570222"})
    assert r.json()["pay_orange_money"] == "+232 76 570222" and r.json()["ready"] is False
    assert client.put("/api/businesses/mine/payments", headers=seller, json={"pay_afrimoney": "12"}).status_code == 422
    assert client.put("/api/businesses/mine/payments", headers=seller, json={"okada_fee_sll": -5}).status_code == 422
    r = client.put("/api/businesses/mine/payments", headers=seller, json={"okada_fee_sll": 10000, "van_fee_sll": 50000})
    assert r.json()["ready"] is False  # still needs the shop's location
    r = client.put("/api/businesses/mine/payments", headers=seller, json={"shop_lat": 8.48, "shop_lng": -13.23})
    assert r.json()["ready"] is True and r.json()["pay_orange_money"] == "+232 76 570222"


def _deliver(client, buyer, order):
    """Pay, prepare and deliver an order (statuses only; rider flow is in test_rider_payouts)."""
    from sqlalchemy import text
    with sync_engine.begin() as c:
        c.execute(text("UPDATE shop_orders SET status='delivered' WHERE id=:i"), {"i": order["id"]})


def test_buyer_pays_salonautozone_once(client):
    seller, buyer, order, _ = _shop_order(client)
    oid = order["id"]
    # the shop's own delivery fee is used, but the buyer pays SalonAutoZone
    assert order["delivery_fee_sll"] == 15000 and order["total_to_pay_sll"] == 115000 and order["pay_to"] == "platform"
    page = client.get(f"/api/pay/orders/{oid}", headers=buyer).json()
    assert page["payee"] == "SalonAutoZone"
    assert [m["number"] for m in page["methods"]] == [get_settings().ORANGE_MONEY_NUMBER]
    pid = client.post(f"/api/pay/orders/{oid}/manual", headers=buyer, json={"method": "orange_money", "reference": _ref(), "payer_phone": "076888999"}).json()["id"]
    # the shop can't approve SalonAutoZone's money
    assert client.post(f"/api/pay/seller/payments/{pid}/approve", headers=seller).status_code == 404
    assert client.post(f"/api/pay/admin/payments/{pid}/approve", headers=ADMIN).status_code == 200


def test_three_way_split_and_paying_the_shop(client):
    client.put("/api/shop/admin/settings", headers=ADMIN, json={"commission_pct": 5})
    try:
        seller, buyer, order, _ = _shop_order(client)
        oid = order["id"]
        client.post(f"/api/shop/admin/orders/{oid}/confirm-payment", headers=ADMIN)
        sale = [o for o in client.get("/api/shop/seller/orders", headers=seller).json()["orders"] if o["id"] == oid][0]
        # parts 100,000: 5% commission -> shop gets 95,000. Delivery 15,000: 10% share -> rider 13,500.
        assert sale["you_get_sll"] == 95000 and sale["commission_sll"] == 5000 and sale["shop_paid"] is False
        assert "platform_fee_sll" not in sale and "driver_payout_sll" not in sale
        p = client.get("/api/shop/seller/payouts", headers=seller).json()
        assert p["coming_sll"] == 95000 and p["due_sll"] == 0
        # not delivered: can't be paid out yet
        assert client.post(f"/api/shop/admin/shop-payouts/{oid}/paid", headers=ADMIN, json={}).status_code == 409

        _deliver(client, buyer, order)
        p = client.get("/api/shop/seller/payouts", headers=seller).json()
        assert p["coming_sll"] == 0 and p["due_sll"] == 95000
        assert client.get("/api/admin/todo", headers=ADMIN).json()["shops_to_pay"] >= 1
        mine = [x for x in client.get("/api/shop/admin/shop-payouts", headers=ADMIN).json()["payouts"] if x["order_id"] == oid][0]
        assert mine["amount_sll"] == 95000 and mine["orange_money"] == "+232 76 570111"
        assert client.post(f"/api/shop/admin/shop-payouts/{oid}/paid", headers=ADMIN, json={"reference": "OM123"}).status_code == 200
        assert client.post(f"/api/shop/admin/shop-payouts/{oid}/paid", headers=ADMIN, json={}).status_code == 409
        p = client.get("/api/shop/seller/payouts", headers=seller).json()
        assert p["due_sll"] == 0 and p["recent_paid"][0]["reference"] == "OM123"

        slip = client.get(f"/api/shop/orders/{oid}/receipt?copy=store", headers=seller).json()
        assert slip["paid_to"] == "platform" and slip["commission_sll"] == 5000 and slip["amount_due_to_store_sll"] == 95000
        assert slip["store_paid_ref"] == "OM123"
        rec = client.get(f"/api/shop/orders/{oid}/receipt", headers=buyer).json()
        assert rec["total_sll"] == 115000
    finally:
        client.put("/api/shop/admin/settings", headers=ADMIN, json={"commission_pct": 0})


def test_shop_without_fees_uses_site_fees(client):
    _, buyer, order, settings = _shop_order(client, fees=False)
    assert settings["ready"] is False
    assert order["pay_to"] == "platform" and order["delivery_fee_sll"] == 25000
