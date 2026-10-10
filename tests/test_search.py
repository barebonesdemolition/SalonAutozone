"""Search finds what people mean, not just exact phrases."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import models
from app.db import sync_engine
from app.main import app
from app.services import search


@pytest.fixture(scope="module")
def client():
    for model in (models.Business, models.VehicleListing, models.PartListing, models.SupplierCatalog):
        model.__table__.create(bind=sync_engine, checkfirst=True)
    with TestClient(app) as c:
        n = uuid.uuid4().hex[:8]
        tok = c.post("/api/auth/register", json={"full_name": "Search Seller", "phone": "+2324" + str(int(n, 16))[:7],
                                                 "email": f"q{n}@example.com", "password": "password123", "account_type": "seller"}).json()["access_token"]
        auth = {"Authorization": "Bearer " + tok}
        tag = n[:5]
        for name, make, model, stock in [
            (f"Brake pads (front) {tag}", "Toyota", "Corolla", 3),
            (f"Alternator 90A {tag}", "Nissan", "X-Trail", 1),
            (f"Spark plugs set {tag}", "Honda", "CR-V", 4),
            (f"All-season tyres 15in {tag}", None, None, 4),
            (f"Sold-out radiator {tag}", "Toyota", "Hilux", 0),
        ]:
            r = c.post("/api/parts/", headers=auth, json={"name": name, "category": "Parts", "price_sll": 1000,
                                                         "compatible_make": make, "compatible_model": model, "stock_quantity": stock})
            assert r.status_code == 201, r.text
        c.tag = tag
        yield c


def names(client, q):
    d = client.get("/api/unified/parts", params={"q": f"{q} {client.tag}"}).json()
    return sorted(p["name"].rsplit(" ", 1)[0] for p in d["local_parts"]), d.get("did_you_mean")


@pytest.mark.parametrize("q,expected", [
    ("toyota brake pads", ["Brake pads (front)"]),          # words in any order, across fields
    ("break pad corolla", ["Brake pads (front)"]),          # common misspelling
    ("breakpads", ["Brake pads (front)"]),
    ("spark plug", ["Spark plugs set"]),                    # singular finds plural
    ("tires", ["All-season tyres 15in"]),                   # tire/tyre
    ("radiator", []),                                       # out of stock is hidden
])
def test_search(client, q, expected):
    assert names(client, q)[0] == expected


def test_typo_is_corrected(client):
    found, fixed = names(client, "altenator")
    assert found == ["Alternator 90A"]
    assert fixed.startswith("alternator")


def test_word_helpers():
    assert search.words("Toyota-Corolla brake PADS!") == ["toyota", "corolla", "brake", "pads"]
    assert "pad" in search.alternatives("pads")
    assert search.correct("radiater", set()) == "radiator"
    assert search.correct("corolla", set()) is None
