"""Delivery priced by distance, and the one-time switch to new leones."""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app import models
from app.db import sync_engine
from app.main import app
from app.shop import RATE_DEFAULTS, fee_for_km, km_between

ADMIN = {"X-Admin-Key": "test-admin-secret"}
CENTRAL = (8.4875, -13.2356)   # around the Cotton Tree
MILE_13 = (8.3700, -13.1100)
LUMLEY = (8.4200, -13.2800)


@pytest.fixture(scope="module")
def client():
    for model in (models.Business, models.VehicleListing, models.PartListing):
        model.__table__.create(bind=sync_engine, checkfirst=True)
    with TestClient(app) as c:
        c.put("/api/shop/admin/settings", headers=ADMIN, json={"platform_pct": 10, "commission_pct": 0, **RATE_DEFAULTS})
        yield c


def test_rates_match_the_real_trip():
    km = km_between(*CENTRAL, *MILE_13)
    assert 18 <= km <= 22
    assert fee_for_km(km, "bike", RATE_DEFAULTS) in (135, 140, 145)
    assert fee_for_km(20, "bike", RATE_DEFAULTS) == 140
    assert fee_for_km(2, "bike", RATE_DEFAULTS) == 30      # minimum
    assert fee_for_km(5, "bike", RATE_DEFAULTS) == 50
    assert fee_for_km(10, "bike", RATE_DEFAULTS) == 80
    assert fee_for_km(20, "van", RATE_DEFAULTS) == 420
    assert fee_for_km(0, "bike", RATE_DEFAULTS) == 30


def _user(client, name):
    n = uuid.uuid4().hex[:8]
    r = client.post("/api/auth/register", json={"full_name": name, "phone": "+2327" + str(int(n, 16))[:7],
                                                "email": f"k{n}@example.com", "password": "password123"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _shop_with_part(client, pin=CENTRAL, name="Brake pads"):
    seller = _user(client, "Distance Shop")
    client.post("/api/businesses/apply", headers=seller, json={"business_type": "store", "name": f"Central Spares {uuid.uuid4().hex[:4]}", "city": "Freetown", "whatsapp": "076 999 111"})
    if pin:
        r = client.put("/api/businesses/mine/payments", headers=seller, json={"pay_orange_money": "076999111", "shop_lat": pin[0], "shop_lng": pin[1]})
        assert r.status_code == 200 and r.json()["has_pin"] and r.json()["ready"]
    part = client.post("/api/parts/", headers=seller, json={"name": name, "category": "Brakes", "price_sll": 450, "stock_quantity": 5}).json()
    return seller, part


def test_quote_and_order_by_distance(client):
    seller, part = _shop_with_part(client)
    buyer = _user(client, "Mile 13 Buyer")
    items = [{"part_id": part["id"], "qty": 1}]
    far = client.post("/api/shop/quote", headers=buyer, json={"items": items, "delivery_lat": MILE_13[0], "delivery_lng": MILE_13[1]}).json()
    near = client.post("/api/shop/quote", headers=buyer, json={"items": items, "delivery_lat": LUMLEY[0], "delivery_lng": LUMLEY[1]}).json()
    assert far["exact"] and far["delivery_fee_sll"] in (135, 140, 145) and far["total_sll"] == 450 + far["delivery_fee_sll"]
    assert near["delivery_fee_sll"] < far["delivery_fee_sll"] and near["distance_km"] < far["distance_km"]
    # no buyer pin: the site's fixed fee if one is set, otherwise a typical 10 km trip
    fixed = client.get("/api/shop/admin/settings", headers=ADMIN).json()["okada_fee_sll"]
    rough = client.post("/api/shop/quote", headers=buyer, json={"items": items}).json()
    assert rough["exact"] is False and rough["delivery_fee_sll"] == (fixed if fixed is not None else 80)
    # the order is charged the same as the quote, and the rider sees the distance
    order = client.post("/api/shop/orders", headers=buyer, json={"items": items, "delivery_address": "Mile 13 junction",
                        "delivery_area": "Mile 13", "phone": "+23276111222", "delivery_lat": MILE_13[0], "delivery_lng": MILE_13[1]}).json()
    assert order["delivery_fee_sll"] == far["delivery_fee_sll"] and order["total_to_pay_sll"] == far["total_sll"]
    from sqlalchemy import text
    with sync_engine.begin() as c:
        km, payout, cut = c.execute(text("SELECT distance_km, driver_payout_sll, platform_fee_sll FROM shop_orders WHERE id=:i"), {"i": order["id"]}).one()
    assert 18 <= km <= 22 and cut == round(far["delivery_fee_sll"] * 0.1) and payout + cut == far["delivery_fee_sll"]


def test_too_far_and_pin_must_be_in_sierra_leone(client):
    seller, part = _shop_with_part(client)
    buyer = _user(client, "Far Buyer")
    r = client.post("/api/shop/quote", headers=buyer, json={"items": [{"part_id": part["id"], "qty": 1}], "delivery_lat": 7.96, "delivery_lng": -11.74})  # Bo
    assert r.status_code == 409 and "too far" in r.json()["detail"]
    assert client.put("/api/businesses/mine/payments", headers=seller, json={"shop_lat": 51.5, "shop_lng": -0.12}).status_code == 422


def test_admin_rates_and_examples(client):
    d = client.get("/api/shop/admin/settings", headers=ADMIN).json()
    assert d["rate_base"] == 20 and d["rate_per_km"] == 6
    assert {x["km"]: x["okada_sll"] for x in d["examples"]}[20] == 140
    d = client.put("/api/shop/admin/settings", headers=ADMIN, json={"rate_per_km": 7}).json()
    assert {x["km"]: x["okada_sll"] for x in d["examples"]}[20] == 160
    client.put("/api/shop/admin/settings", headers=ADMIN, json={"rate_per_km": 6})


def test_one_time_switch_to_new_leones(tmp_path, monkeypatch):
    """Old amounts are divided by 1,000 exactly once."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    import app.currency as cur

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/old.db")
    monkeypatch.setattr(cur, "engine", eng)

    async def run():
        async with eng.begin() as c:
            await c.execute(text("CREATE TABLE part_listings (id INTEGER PRIMARY KEY, price_sll FLOAT)"))
            await c.execute(text("CREATE TABLE plans (id INTEGER PRIMARY KEY, price_sll INTEGER)"))
            await c.execute(text("CREATE TABLE delivery_settings (key VARCHAR PRIMARY KEY, value FLOAT)"))
            await c.execute(text("INSERT INTO part_listings VALUES (1, 450000), (2, NULL), (3, 1500)"))
            await c.execute(text("INSERT INTO plans VALUES (1, 75000), (2, 0)"))
            await c.execute(text("INSERT INTO delivery_settings VALUES ('okada_fee_sll', 25000), ('platform_pct', 10)"))
        assert await cur.convert_to_new_leones() is True
        assert await cur.convert_to_new_leones() is False  # never twice
        async with eng.begin() as c:
            parts = [r[0] for r in (await c.execute(text("SELECT price_sll FROM part_listings ORDER BY id"))).all()]
            plans = [r[0] for r in (await c.execute(text("SELECT price_sll FROM plans ORDER BY id"))).all()]
            sets = dict((await c.execute(text("SELECT key, value FROM delivery_settings"))).all())
        await eng.dispose()
        return parts, plans, sets

    parts, plans, sets = asyncio.run(run())
    assert parts == [450, None, 1.5]
    assert plans == [75, 0]
    assert sets == {"okada_fee_sll": 25, "platform_pct": 10}   # percentages untouched


def test_catalogue_sll_column_is_converted():
    from app.routers.catalog import parse_csv
    old = parse_csv("Brake pads\nPart Number,Vehicle,Brand,Price SLL,Price CAD\n04465-02220,Toyota Corolla,Akebono,450000,25\n")
    new = parse_csv("Brake pads\nPart Number,Vehicle,Brand,Price Le,Price CAD\n04465-02220,Toyota Corolla,Akebono,450,25\n")
    assert old[0]["price_1_sll"] == 450 and new[0]["price_1_sll"] == 450
