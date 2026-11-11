"""Live delivery tracking, and our own traffic counting."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import models
from app.db import sync_engine
from app.main import app

ADMIN = {"X-Admin-Key": "test-admin-secret"}
PHONE_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) Mobile Safari"}


@pytest.fixture(scope="module")
def client():
    for model in (models.Business, models.VehicleListing, models.PartListing):
        model.__table__.create(bind=sync_engine, checkfirst=True)
    with TestClient(app) as c:
        c.put("/api/shop/admin/settings", headers=ADMIN, json={"platform_pct": 0, "commission_pct": 5})
        yield c


def _user(client, name):
    n = uuid.uuid4().hex[:8]
    r = client.post("/api/auth/register", json={"full_name": name, "phone": "+2327" + str(int(n, 16))[:7],
                                                "email": f"t{n}@example.com", "password": "password123"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _job_on_the_road(client):
    seller, buyer, rider = _user(client, "Track Shop"), _user(client, "Track Buyer"), _user(client, "Track Rider")
    client.post("/api/businesses/apply", headers=seller, json={"business_type": "store", "name": f"Track Spares {uuid.uuid4().hex[:4]}", "city": "Freetown", "whatsapp": "076 777 888"})
    client.put("/api/businesses/mine/payments", headers=seller, json={"pay_orange_money": "076777888", "shop_lat": 8.4875, "shop_lng": -13.2356})
    part = client.post("/api/parts/", headers=seller, json={"name": "Wiper blades", "category": "Body", "price_sll": 120, "stock_quantity": 9}).json()
    order = client.post("/api/shop/orders", headers=buyer, json={"items": [{"part_id": part["id"], "qty": 1}], "delivery_address": "Mile 13",
                        "delivery_area": "Mile 13", "phone": "+23276123123", "delivery_lat": 8.37, "delivery_lng": -13.11}).json()
    client.post(f"/api/shop/admin/orders/{order['id']}/confirm-payment", headers=ADMIN)
    client.post(f"/api/shop/seller/orders/{order['id']}/ready", headers=seller)
    phone = "+2327" + str(uuid.uuid4().int)[:7]
    client.post("/api/drivers/apply", headers=rider, json={"vehicle_type": "bike", "city": "Freetown", "phone": phone})
    d = [x for x in client.get("/api/drivers/admin/list?status=pending", headers=ADMIN).json()["drivers"] if x.get("phone") == phone][0]
    client.post(f"/api/drivers/admin/{d['id']}/status?status=verified", headers=ADMIN)
    client.post("/api/drivers/me/online?online=true", headers=rider)
    job = [j for j in client.get("/api/shop/driver/jobs?scope=open", headers=rider).json()["jobs"] if j["code"] == order["code"]][0]
    assert 18 <= job["distance_km"] <= 22
    client.post(f"/api/shop/driver/jobs/{job['id']}/accept", headers=rider)
    return seller, buyer, rider, order, job, part


def test_live_tracking(client):
    seller, buyer, rider, order, job, _ = _job_on_the_road(client)
    oid = order["id"]
    t = client.get(f"/api/shop/orders/{oid}/tracking", headers=buyer).json()
    assert t["live"] and t["rider"]["stage"] == "accepted" and "lat" not in t["rider"]  # phone hasn't reported yet
    assert t["shop"]["lat"] == 8.4875 and t["buyer"]["lat"] == 8.37
    # only the buyer, the shop and admins can see it
    assert client.get(f"/api/shop/orders/{oid}/tracking", headers=_user(client, "Nosy")).status_code == 404
    assert client.get(f"/api/shop/orders/{oid}/tracking", headers=seller).status_code == 200
    # someone else can't move the rider
    assert client.post(f"/api/shop/driver/jobs/{job['id']}/location", headers=buyer, json={"lat": 8.4, "lng": -13.2}).status_code in (403, 404, 409)
    client.post(f"/api/shop/driver/jobs/{job['id']}/pickup", headers=rider)
    assert client.post(f"/api/shop/driver/jobs/{job['id']}/location", headers=rider, json={"lat": 8.42, "lng": -13.17}).status_code == 200
    t = client.get(f"/api/shop/orders/{oid}/tracking", headers=buyer).json()
    assert t["rider"]["lat"] == 8.42 and t["rider"]["seen_s"] < 60 and 5 < t["rider"]["km_left"] < 15
    live = client.get("/api/shop/admin/live", headers=ADMIN).json()
    assert any(x["code"] == order["code"] for x in live["deliveries"])
    # deliver with the phone's position: recorded as where it was handed over, and tracking stops
    code = client.get(f"/api/shop/orders/{oid}", headers=buyer).json()["delivery_code"]
    assert client.post(f"/api/shop/driver/jobs/{job['id']}/deliver", headers=rider, json={"code": code, "lat": 8.3702, "lng": -13.1101}).status_code == 200
    t = client.get(f"/api/shop/orders/{oid}/tracking", headers=buyer).json()
    assert t["live"] is False and t["rider"] is None and t["delivered_at"] == {"lat": 8.3702, "lng": -13.1101}
    assert client.post(f"/api/shop/driver/jobs/{job['id']}/location", headers=rider, json={"lat": 8.4, "lng": -13.2}).status_code == 409


def test_visits_searches_and_shop_numbers(client):
    seller, buyer, rider, order, job, part = _job_on_the_road(client)
    vid = "v" + uuid.uuid4().hex[:10]
    for path in ("/", f"/part/{part['id']}", "/checkout"):
        assert client.post("/api/analytics/hit", headers=PHONE_UA, json={"kind": "view", "path": path, "visitor": vid, "ref": "https://www.facebook.com/x"}).status_code == 204
    client.post("/api/analytics/hit", headers=PHONE_UA, json={"kind": "contact", "path": f"/part/{part['id']}", "visitor": vid, "detail": "whatsapp"})
    # link-preview bots are ignored
    client.post("/api/analytics/hit", headers={"User-Agent": "WhatsApp/2.23"}, json={"kind": "view", "path": f"/part/{part['id']}", "visitor": "bot"})
    # searches from the page are counted, including ones with no results
    word = "zz" + uuid.uuid4().hex[:6]
    client.get(f"/api/unified/parts?q={word}", headers={**PHONE_UA, "X-SAZ-Page": "1", "X-SAZ-Visitor": vid})
    client.get("/api/unified/parts?q=wiper", headers={**PHONE_UA, "X-SAZ-Page": "1", "X-SAZ-Visitor": vid})
    client.get("/api/unified/parts?q=internalcall", headers=PHONE_UA)   # not from a page: not counted

    month = client.get("/api/analytics/seller/month", headers=seller).json()
    assert month["this_month"]["views"] == 1 and month["this_month"]["contacts"] == 1 and month["this_month"]["people"] == 1
    assert month["this_month"]["orders"] == 1 and month["top_parts"][0]["part_id"] == part["id"]

    s = client.get("/api/analytics/admin/summary?days=7", headers=ADMIN).json()
    assert s["totals"]["visitors"] >= 1 and s["totals"]["contacts"] >= 1
    assert any(x["query"] == word for x in s["not_found"]) and any(x["query"] == "wiper" for x in s["top_searches"])
    assert not any(x["query"] == "internalcall" for x in s["top_searches"] + s["not_found"])
    assert any(x["site"] == "www.facebook.com" for x in s["sources"])
    assert any(a["area"] == "Mile 13" for a in s["areas"]) and [8.37, -13.11] in s["pins"]
    assert s["funnel"][0]["step"] == "Visited the site"
    assert client.get("/api/analytics/admin/summary", headers=PHONE_UA).status_code in (401, 403)


def test_map_script_served(client):
    r = client.get("/static/trackmap.js?v=1")
    assert r.status_code == 200 and "SAZMap" in r.text
