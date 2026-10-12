"""Supplier catalogue import: admin-only, keeps or replaces, skips repeats, search works."""
import pytest
from fastapi.testclient import TestClient

from app import models
from app.db import sync_engine
from app.main import app

ADMIN = {"X-Admin-Key": "test-admin-secret"}
CSV = (
    "Brake pads\n"
    "Part Number,Vehicle,Brand,Price SLL,Price CAD,Brand 2,Price 2 SLL\n"
    "TST-04465-1,Toyota Corolla 2007-2013,Akebono,\"450,000\",25\n"
    "TST-04465-2,Honda Fit 2008,,,,Brembo,300000\n"
    "Filters\n"
    "TST-90915-1,Toyota (many),Denso,45000,3\n"
)


@pytest.fixture(scope="module")
def client():
    models.SupplierCatalog.__table__.create(bind=sync_engine, checkfirst=True)
    with TestClient(app) as c:
        yield c


def _upload(client, body, replace, headers=ADMIN):
    return client.post(f"/api/catalog/upload?replace={str(replace).lower()}", headers=headers,
                       files={"file": ("cat.csv", body.encode(), "text/csv")})


def test_upload_needs_admin(client):
    assert _upload(client, CSV, False, headers={}).status_code in (401, 403)


def test_import_add_skip_and_replace(client):
    r = _upload(client, CSV, False)
    assert r.status_code == 200, r.text
    first = r.json()["imported"]
    assert r.json()["total_parts"] == 3

    again = _upload(client, CSV, False).json()
    assert again["imported"] == 0 and again["skipped"] == 3

    items = client.get("/api/catalog/search?q=TST-04465").json()["items"]
    by_pn = {i["part_number"]: i for i in items}
    assert by_pn["TST-04465-1"]["category"] == "Brake pads"
    assert by_pn["TST-04465-2"]["brand_1"] == "Brembo"  # first brand with a price
    assert "price_sll" not in by_pn["TST-04465-1"]  # supplier prices stay private

    cats = {c["name"] for c in client.get("/api/catalog/categories").json()["categories"]}
    assert {"Brake pads", "Filters"} <= cats

    one = client.get("/api/catalog/search?q=TST-90915").json()["items"][0]
    detail = client.get(f"/api/unified/catalog/{one['id']}").json()
    assert detail["part_number"] == "TST-90915-1"

    rep = _upload(client, "Filters\nTST-ONLY-1,Nissan,,10000\n", True).json()
    assert rep["imported"] == 1
    assert client.get("/api/catalog/stats").json()["total_parts"] == 1
    assert first >= 3


def test_empty_file_gives_plain_message(client):
    r = _upload(client, "nothing useful\n", False)
    assert r.status_code == 400 and "part numbers" in r.json()["detail"]
