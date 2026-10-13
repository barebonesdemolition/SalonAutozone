"""Login help: any phone format, pause after wrong passwords, forgot/reset/change password, admin tools."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

ADMIN = {"X-Admin-Key": "test-admin-secret"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _new_user(client, password="password123"):
    n = int(uuid.uuid4().hex[:6], 16) % 900000 + 100000
    phone = f"+232 77 {n}"
    r = client.post("/api/auth/register", json={"full_name": "Hawa Sesay", "phone": phone,
                                                "email": f"h{n}{uuid.uuid4().hex[:4]}@example.com", "password": password})
    assert r.status_code == 201, r.text
    return phone, n, r.json()


def _login(client, who, password="password123"):
    return client.post("/api/auth/login", json={"phone": who, "password": password})


def _auth(token):
    return {"Authorization": "Bearer " + token}


def test_any_phone_format_signs_in_and_duplicates_are_caught(client):
    phone, n, reg = _new_user(client)
    for typed in (phone, f"077{n}", f"77 {n}", f"23277{n}"):
        assert _login(client, typed).status_code == 200, typed
    assert _login(client, reg["user"]["email"].upper()).status_code == 200
    dup = client.post("/api/auth/register", json={"full_name": "Other", "phone": f"077{n}",
                                                  "email": f"x{uuid.uuid4().hex[:6]}@example.com", "password": "password123"})
    assert dup.status_code == 409


def test_wrong_passwords_pause_the_account_and_admin_can_unlock(client):
    phone, n, reg = _new_user(client)
    for _ in range(8):
        assert _login(client, phone, "nope-nope").status_code == 401
    paused = _login(client, phone)  # even the right password waits
    assert paused.status_code == 429 and "15 minutes" in paused.json()["detail"]

    uid = reg["user"]["id"]
    detail = client.get(f"/api/admin/users/{uid}", headers=ADMIN).json()
    assert detail["user"]["locked"] is True and detail["user"]["failed_logins"] == 8
    assert {e["outcome"] for e in detail["events"]} >= {"wrong_password", "locked"}

    assert client.post(f"/api/admin/users/{uid}/unlock", headers=ADMIN).status_code == 200
    assert _login(client, phone).status_code == 200
    assert client.get(f"/api/admin/users/{uid}", headers=ADMIN).json()["user"]["failed_logins"] == 0


def test_unknown_number_is_logged_for_the_admin(client):
    typed = f"+232 99 {uuid.uuid4().int % 900000 + 100000}"
    assert _login(client, typed).status_code == 401
    failed = client.get("/api/admin/login-problems", headers=ADMIN).json()["failed"]
    assert any(e["identity"] == typed and e["outcome"] == "no_account" for e in failed)


def test_forgot_and_reset_password(client):
    phone, n, reg = _new_user(client)
    uid, old_token = reg["user"]["id"], reg["access_token"]

    r = client.post("/api/auth/forgot", json={"identity": f"077{n}"})
    assert r.status_code == 200 and r.json()["whatsapp"]
    # Same answer when there is no such account.
    assert client.post("/api/auth/forgot", json={"identity": "nobody@example.com"}).status_code == 200
    assert client.get("/api/admin/todo", headers=ADMIN).json()["password_requests"] >= 1
    reqs = client.get("/api/admin/login-problems", headers=ADMIN).json()["requests"]
    assert any(q["user_id"] == uid for q in reqs)
    assert client.get(f"/api/admin/users?q=077{n}", headers=ADMIN).json()["users"][0]["reset_requested"] is True

    link = client.post(f"/api/admin/users/{uid}/reset-link", headers=ADMIN).json()
    token = link["url"].split("token=")[1]
    assert link["whatsapp_url"].startswith(f"https://wa.me/23277{n}?text=")
    assert client.get(f"/api/auth/reset/{token}").json()["first_name"] == "Hawa"

    assert client.post("/api/auth/reset", json={"token": token, "password": "short"}).status_code == 422
    done = client.post("/api/auth/reset", json={"token": token, "password": "brand-new-pass"})
    assert done.status_code == 200
    assert client.get("/api/auth/me", headers=_auth(done.json()["access_token"])).status_code == 200

    assert client.get("/api/auth/me", headers=_auth(old_token)).status_code == 401   # old logins end
    assert client.post("/api/auth/reset", json={"token": token, "password": "another-pass1"}).status_code == 400  # one use
    assert _login(client, phone).status_code == 401
    assert _login(client, phone, "brand-new-pass").status_code == 200
    assert not any(q["user_id"] == uid for q in client.get("/api/admin/login-problems", headers=ADMIN).json()["requests"])


def test_only_the_newest_reset_link_works(client):
    _, _, reg = _new_user(client)
    uid = reg["user"]["id"]
    first = client.post(f"/api/admin/users/{uid}/reset-link", headers=ADMIN).json()["url"].split("token=")[1]
    second = client.post(f"/api/admin/users/{uid}/reset-link", headers=ADMIN).json()["url"].split("token=")[1]
    assert client.get(f"/api/auth/reset/{first}").status_code == 400
    assert client.get(f"/api/auth/reset/{second}").status_code == 200
    assert client.get("/api/auth/reset/" + "x" * 43).status_code == 400


def test_change_password_signs_out_other_devices(client):
    phone, _, reg = _new_user(client)
    other_device = _login(client, phone).json()["access_token"]
    bad = client.post("/api/auth/change-password", headers=_auth(reg["access_token"]),
                      json={"current_password": "wrong", "new_password": "another-pass1"})
    assert bad.status_code == 400
    ok = client.post("/api/auth/change-password", headers=_auth(reg["access_token"]),
                     json={"current_password": "password123", "new_password": "another-pass1"})
    assert ok.status_code == 200
    assert client.get("/api/auth/me", headers=_auth(ok.json()["access_token"])).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(other_device)).status_code == 401
    assert _login(client, phone, "another-pass1").status_code == 200


def test_switch_account_off_and_on(client):
    phone, _, reg = _new_user(client)
    uid = reg["user"]["id"]
    assert client.post(f"/api/admin/users/{uid}/active?active=false", headers=ADMIN).status_code == 200
    off = _login(client, phone)
    assert off.status_code == 403 and "switched off" in off.json()["detail"]
    assert client.get("/api/auth/me", headers=_auth(reg["access_token"])).status_code == 401
    assert client.post(f"/api/admin/users/{uid}/active?active=true", headers=ADMIN).status_code == 200
    assert _login(client, phone).status_code == 200


def test_admin_tools_need_admin(client):
    _, _, reg = _new_user(client)
    me = _auth(reg["access_token"])
    for method, url in (("get", "/api/admin/users"), ("get", "/api/admin/login-problems"),
                        ("post", f"/api/admin/users/{reg['user']['id']}/reset-link"),
                        ("post", f"/api/admin/users/{reg['user']['id']}/unlock")):
        assert getattr(client, method)(url, headers=me).status_code == 403
        assert getattr(client, method)(url).status_code == 403


def test_pages_exist(client):
    for path in ("/forgot-password", "/reset-password", "/account/password"):
        assert client.get(path).status_code == 200
    assert "Forgot password?" in client.get("/login").text
