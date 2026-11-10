"""Rider sign-up: read the ID card and number plate from photos; admin sees checks."""
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app import drivers
from app.main import app

ADMIN = {"X-Admin-Key": "test-admin-secret"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _rider(client, name="Abu Bangura"):
    n = int(uuid.uuid4().hex[:6], 16) % 900000 + 100000
    tok = client.post("/api/auth/register", json={"full_name": name, "phone": f"+232 77 {n}",
                                                  "email": f"r{n}{uuid.uuid4().hex[:4]}@example.com", "password": "password123"}).json()["access_token"]
    return {"Authorization": "Bearer " + tok}


def _jpeg():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (60, 40), (90, 90, 200)).save(b, "JPEG")
    return b.getvalue()


def _apply(client, auth, **extra):
    body = {"vehicle_type": "bike", "plate_number": "akb 123", "city": "Freetown", "phone": "077 555 111",
            "id_photo_url": "https://res.cloudinary.com/demo/id.jpg", "plate_photo_url": "https://res.cloudinary.com/demo/plate.jpg",
            "id_type": "national_id", "id_name": "Abu Bangura", "id_number": "ab-123 456", "id_expiry": "2031-01-31"}
    body.update(extra)
    return client.post("/api/drivers/apply", headers=auth, json=body)


def test_clean_scans():
    assert drivers.clean_id_scan({"document_type": "not_an_id"})["ok"] is False
    idd = drivers.clean_id_scan({"document_type": "drivers_licence", "full_name": "ABU  BANGURA", "id_number": "dl/2020-55 ",
                                 "expiry_date": "2020-01-01", "date_of_birth": "1990-13", "readable": True})
    assert idd["id_type"] == "drivers_licence" and idd["full_name"] == "ABU BANGURA" and idd["id_number"] == "DL/2020-55"
    assert idd["expired"] is True and idd["date_of_birth"] is None
    blank = drivers.clean_id_scan({"document_type": "national_id", "full_name": "null", "id_number": None})
    assert blank["ok"] is False and blank["problem"]
    pl = drivers.clean_plate_scan({"plate_number": " akb-123 ", "vehicle": "motorbike", "colour": "red", "make": "Haojue"})
    assert pl["plate_number"] == "AKB-123" and pl["vehicle_type"] == "bike" and pl["ok"] is True
    assert drivers.clean_plate_scan({"plate_number": "unknown"})["ok"] is False


def test_names_match():
    assert drivers.names_match("Abu Bangura", "BANGURA ABU")
    assert drivers.names_match("Abu Bangura", "Abu Musa Bangura")
    assert drivers.names_match("Abu Bangura", "Fatmata Sesay") is False
    assert drivers.names_match("", "x y") is None


def test_scan_endpoint(client, monkeypatch):
    from app.config import get_settings
    auth = _rider(client)
    monkeypatch.setattr(get_settings(), "GEMINI_API_KEY", "")
    assert client.post("/api/drivers/scan?kind=id", headers=auth, files={"file": ("i.jpg", _jpeg(), "image/jpeg")}).status_code == 503

    monkeypatch.setattr(get_settings(), "GEMINI_API_KEY", "test")
    seen = []

    def fake(image, mime, prompt):
        seen.append(prompt)
        if "identity document" in prompt:
            return {"document_type": "national_id", "full_name": "Abu Bangura", "id_number": "SL1234567", "expiry_date": "2032-05-01", "readable": True}
        return {"plate_number": "AKB 123", "vehicle": "motorbike", "colour": "red", "readable": True}

    monkeypatch.setattr(drivers, "_ask_ai", fake)
    r = client.post("/api/drivers/scan?kind=id", headers=auth, files={"file": ("i.jpg", _jpeg(), "image/jpeg")})
    assert r.status_code == 200 and r.json()["id_number"] == "SL1234567" and r.json()["id_type"] == "national_id"
    p = client.post("/api/drivers/scan?kind=plate", headers=auth, files={"file": ("p.jpg", _jpeg(), "image/jpeg")})
    assert p.json()["plate_number"] == "AKB 123" and p.json()["vehicle_type"] == "bike"
    assert len(seen) == 2

    assert client.post("/api/drivers/scan?kind=selfie", headers=auth, files={"file": ("p.jpg", _jpeg(), "image/jpeg")}).status_code == 422
    assert client.post("/api/drivers/scan?kind=id", headers=auth, files={"file": ("p.txt", b"x", "text/plain")}).status_code == 400
    assert client.post("/api/drivers/scan?kind=id", files={"file": ("i.jpg", _jpeg(), "image/jpeg")}).status_code == 401

    def broken(image, mime, prompt):
        raise RuntimeError("model down")
    monkeypatch.setattr(drivers, "_ask_ai", broken)
    down = client.post("/api/drivers/scan?kind=id", headers=auth, files={"file": ("i.jpg", _jpeg(), "image/jpeg")})
    assert down.status_code == 502 and "model" not in down.json()["detail"]


def test_scan_daily_limit(client, monkeypatch):
    from app.config import get_settings
    auth = _rider(client)
    monkeypatch.setattr(get_settings(), "GEMINI_API_KEY", "test")
    monkeypatch.setattr(drivers, "_ask_ai", lambda i, m, p: {"plate_number": "X1"})
    monkeypatch.setattr(drivers, "SCANS_PER_DAY", 2)
    for _ in range(2):
        assert client.post("/api/drivers/scan?kind=plate", headers=auth, files={"file": ("p.jpg", _jpeg(), "image/jpeg")}).status_code == 200
    assert client.post("/api/drivers/scan?kind=plate", headers=auth, files={"file": ("p.jpg", _jpeg(), "image/jpeg")}).status_code == 429


def test_apply_saves_id_and_plate(client):
    auth = _rider(client)
    r = _apply(client, auth)
    assert r.status_code == 201, r.text
    me = client.get("/api/drivers/me", headers=auth).json()
    assert me["plate_number"] == "AKB 123" and me["id_number"] == "AB-123 456"
    assert me["plate_photo_url"].endswith("plate.jpg") and me["id_type"] == "national_id"


@pytest.mark.parametrize("extra,word", [
    ({"id_expiry": "2001-01-01"}, "expired"), ({"id_type": "library_card"}, "ID type"),
    ({"id_expiry": "31/01/2031"}, "2030-12-31"), ({"plate_photo_url": "javascript:alert(1)"}, "https"),
])
def test_apply_rejects_bad_details(client, extra, word):
    r = _apply(client, _rider(client), **extra)
    assert r.status_code == 422 and word in r.json()["detail"]


def test_admin_sees_checks(client):
    a1, a2 = _rider(client, "Abu Bangura"), _rider(client, "Sorie Kamara")
    tag = uuid.uuid4().hex[:6].upper()
    assert _apply(client, a1, id_number=f"SL-{tag}", plate_number=f"AK {tag}").status_code == 201
    assert _apply(client, a2, id_number=f"sl-{tag.lower()}", plate_number=f"ak {tag.lower()}", id_name="Musa Kanu").status_code == 201
    rows = {d["full_name"]: d for d in client.get("/api/drivers/admin/list?status=pending", headers=ADMIN).json()["drivers"]
            if d.get("id_number") == f"SL-{tag}"}
    first, second = rows["Abu Bangura"], rows["Sorie Kamara"]
    assert first["name_matches"] is True and second["name_matches"] is False
    assert second["same_id_as"] == [first["id"]] and second["same_plate_as"] == [first["id"]]
    assert first["id_type_label"] == "National ID card"


def test_old_style_application_still_works(client):
    r = client.post("/api/drivers/apply", headers=_rider(client), json={"vehicle_type": "bike", "city": "Bo", "phone": "077 111 222"})
    assert r.status_code == 201
