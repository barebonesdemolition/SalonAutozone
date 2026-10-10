"""Account features that touch several tables: saving, enquiries, stats, subscriptions."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import models
from app.db import DATABASE_URL, sync_engine
from app.main import app

POSTGRES = DATABASE_URL.startswith("postgresql")


@pytest.fixture(scope="module")
def client():
    for model in (models.Business, models.VehicleListing, models.PartListing, models.Inquiry,
                  models.SavedListing, models.ImportRequest):
        model.__table__.create(bind=sync_engine, checkfirst=True)
    with TestClient(app) as c:
        yield c


def _auth(client, kind="buyer"):
    n = uuid.uuid4().hex[:8]
    phone = "+2325" + str(int(n, 16))[:7]
    r = client.post("/api/auth/register", json={"full_name": "Acct User", "phone": phone, "email": f"a{n}@example.com",
                                                "password": "password123", "account_type": kind})
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}, phone


def _part(client, auth):
    return client.post("/api/parts/", headers=auth, json={"name": "Alternator", "category": "Electrical", "price_sll": 900000}).json()["id"]


def _car(client, auth):
    return client.post("/api/vehicles/", headers=auth, json={"title": "2014 Hilux", "make": "Toyota", "model": "Hilux", "year": 2014, "price_sll": 1}).json()["id"]


def test_save_a_part_and_a_car(client):
    seller, _ = _auth(client, "seller")
    buyer, _ = _auth(client)
    pid, vid = _part(client, seller), _car(client, seller)
    r1 = client.post("/api/my-account/saved", headers=buyer, json={"listing_type": "part", "listing_id": pid})
    r2 = client.post("/api/my-account/saved", headers=buyer, json={"listing_type": "vehicle", "listing_id": vid})
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    saved = client.get("/api/my-account/saved", headers=buyer).json()["items"]
    assert {(s["listing_type"], s["listing_id"]) for s in saved} == {("part", pid), ("vehicle", vid)}


def test_enquiry_about_a_part_reaches_the_seller(client):
    seller, _ = _auth(client, "seller")
    pid = _part(client, seller)
    r = client.post("/api/inquiries/", json={"listing_type": "part", "listing_id": pid, "buyer_name": "Fatmata",
                                            "buyer_phone": "+23276000111", "buyer_message": "Is it still available?"})
    assert r.status_code == 200, r.text
    got = client.get("/api/my-account/received-inquiries", headers=seller).json()["inquiries"]
    assert [i["buyer_name"] for i in got] == ["Fatmata"]


@pytest.mark.skipif(not POSTGRES, reason="uses Postgres-only SQL (regexp_replace)")
def test_stats_and_requests_on_postgres(client):
    auth, phone = _auth(client)
    client.post("/api/import-requests/", json={"customer_name": "Acct User", "customer_phone": phone, "part_name": "Turbo",
                                               "car_make": "Toyota", "car_model": "Hilux", "car_year": 2014})
    assert client.get("/api/my-account/stats", headers=auth).status_code == 200
    reqs = client.get("/api/my-account/my-import-requests", headers=auth).json()["requests"]
    assert [r["part_name"] for r in reqs] == ["Turbo"]


@pytest.mark.skipif(not POSTGRES, reason="subscription tables are created on Postgres only")
def test_plans_on_postgres(client):
    plans = client.get("/api/plans").json()["plans"]
    assert [p["code"] for p in plans][:2] == ["free", "starter"]
