"""Several photos per listing; the first becomes the cover."""
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import models
from app.db import sync_engine
from app.main import app


@pytest.fixture(scope="module")
def client():
    for model in (models.Business, models.VehicleListing, models.PartListing):
        model.__table__.create(bind=sync_engine, checkfirst=True)
    with TestClient(app) as c:
        yield c


def _token(client):
    n = uuid.uuid4().hex[:8]
    r = client.post("/api/auth/register", json={
        "full_name": "Photo Seller", "phone": "+2328" + str(int(n, 16))[:7], "email": f"p{n}@example.com",
        "password": "password123", "account_type": "seller"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, "JPEG")
    return ("p.jpg", buf.getvalue(), "image/jpeg")


def _part(client, auth):
    r = client.post("/api/parts/", headers=auth, json={"name": "Mirror", "category": "Body", "price_sll": 50000})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_gallery_upload_cover_and_delete(client):
    auth = _token(client)
    pid = _part(client, auth)
    first = client.post(f"/api/photos/part/{pid}", headers=auth, files={"file": _jpeg("red")}).json()
    second = client.post(f"/api/photos/part/{pid}", headers=auth, files={"file": _jpeg("blue")}).json()
    assert first["cover"] is True and second["cover"] is False

    photos = client.get(f"/api/photos/part/{pid}").json()["photos"]
    assert [p["id"] for p in photos] == [first["id"], second["id"]]
    assert client.get(f"/api/parts/{pid}").json()["image_url"] == first["url"]

    client.post(f"/api/photos/part/{pid}/cover/{second['id']}", headers=auth)
    assert client.get(f"/api/parts/{pid}").json()["image_url"] == second["url"]
    assert client.get(f"/api/photos/part/{pid}").json()["photos"][0]["id"] == second["id"]

    r = client.delete(f"/api/photos/part/{pid}/{second['id']}", headers=auth)
    assert r.json()["cover"] == first["url"]


def test_only_the_owner_can_add_photos(client):
    owner, other = _token(client), _token(client)
    pid = _part(client, owner)
    r = client.post(f"/api/photos/part/{pid}", headers=other, files={"file": _jpeg("green")})
    assert r.status_code == 403


def test_photo_limit(client, monkeypatch):
    monkeypatch.setattr("app.photos.MAX_PHOTOS", 2)
    auth = _token(client)
    pid = _part(client, auth)
    for _ in range(2):
        assert client.post(f"/api/photos/part/{pid}", headers=auth, files={"file": _jpeg("red")}).status_code == 201
    assert client.post(f"/api/photos/part/{pid}", headers=auth, files={"file": _jpeg("red")}).status_code == 400
