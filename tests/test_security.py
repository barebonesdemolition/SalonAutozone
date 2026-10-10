"""Checks for the security fixes: removed fake/diagnostic endpoints and locked-down admin data."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("method,path", [
    ("post", "/api/orders/parts"),
    ("post", "/api/orders/ord-1/confirm-payment"),
    ("patch", "/api/orders/ord-1/fulfillment"),
    ("post", "/api/listings/sell"),
    ("post", "/api/ai/advisor"),
    ("get", "/api/parts/by-vin/JTDBR32E173000001"),
    ("get", "/api/vin-suggestions?vin=JTDBR32E"),
    ("get", "/_check_business"),
    ("get", "/_link_vehicles_to_business"),
    ("get", "/_check_inquiries"),
    ("get", "/_migrate_subscriptions"),
])
def test_fake_and_diagnostic_endpoints_are_gone(client, method, path):
    response = getattr(client, method)(path, json={}) if method != "get" else client.get(path)
    assert response.status_code in (404, 405)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/import-requests/"),
    ("get", "/api/import-requests/stats"),
    ("get", "/api/import-requests/1"),
    ("put", "/api/import-requests/1"),
    ("delete", "/api/import-requests/1"),
    ("get", "/api/inquiries/"),
    ("get", "/api/inquiries/stats"),
    ("get", "/api/inquiries/listing/vehicle/1"),
    ("put", "/api/inquiries/1"),
    ("delete", "/api/inquiries/1"),
])
def test_customer_data_needs_admin(client, method, path):
    kwargs = {"json": {}} if method == "put" else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403


def test_seller_inquiries_need_login(client):
    assert client.get("/api/inquiries/seller/1").status_code == 401


def test_ai_status_does_not_leak_key(client):
    body = client.get("/api/ai/status").json()
    assert "key_prefix" not in body and "key_length" not in body


def test_cors_is_not_open_to_every_site(client):
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_settings_ignore_stray_whitespace(monkeypatch):
    # A trailing newline pasted into GEMINI_MODEL on Render broke every AI request.
    from app.config import Settings
    monkeypatch.setenv("GEMINI_MODEL", "gemini-model-name\n")
    monkeypatch.setenv("GEMINI_API_KEY", "  key-123 \n")
    s = Settings()
    assert s.GEMINI_MODEL == "gemini-model-name" and s.GEMINI_API_KEY == "key-123"
