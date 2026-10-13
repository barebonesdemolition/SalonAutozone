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


# ---------------------------------------------------------------- emailed codes

@pytest.fixture
def email_on(monkeypatch):
    """Turn email on and catch what would be sent, instead of calling Brevo."""
    from app import emailer
    from app.config import get_settings
    sent = []
    cfg = get_settings()
    monkeypatch.setattr(cfg, "BREVO_API_KEY", "test-key")
    monkeypatch.setattr(cfg, "EMAIL_FROM", "noreply@example.com")

    async def fake_send(to_email, to_name, subject, text, html_body):
        sent.append({"to": to_email, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(emailer, "send_email", fake_send)
    return sent


def _code_from(mail):
    import re
    return re.search(r"\b(\d{6})\b", mail["subject"]).group(1)


def test_email_code_reset(client, email_on):
    phone, n, reg = _new_user(client)
    old_token = reg["access_token"]
    r = client.post("/api/auth/forgot", json={"identity": f"077{n}"})
    assert r.status_code == 200 and r.json()["method"] == "email"
    assert len(email_on) == 1 and email_on[0]["to"] == reg["user"]["email"]
    code = _code_from(email_on[0])
    assert code in email_on[0]["text"]

    # Asking again straight away is refused (no email spam).
    assert client.post("/api/auth/forgot", json={"identity": f"077{n}"}).status_code == 429

    wrong = "000000" if code != "000000" else "111111"
    bad = client.post("/api/auth/reset-with-code", json={"identity": phone, "code": wrong, "password": "brand-new-1"})
    assert bad.status_code == 400 and "4 tries left" in bad.json()["detail"]

    ok = client.post("/api/auth/reset-with-code", json={"identity": reg["user"]["email"], "code": code, "password": "brand-new-1"})
    assert ok.status_code == 200
    assert client.get("/api/auth/me", headers=_auth(ok.json()["access_token"])).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(old_token)).status_code == 401
    # A code works once.
    assert client.post("/api/auth/reset-with-code", json={"identity": phone, "code": code, "password": "again-again"}).status_code == 400
    assert _login(client, phone, "brand-new-1").status_code == 200
    events = {e["outcome"] for e in client.get(f"/api/admin/users/{reg['user']['id']}", headers=ADMIN).json()["events"]}
    assert {"reset_code_sent", "reset_code_wrong", "password_reset_code"} <= events


def test_code_locks_after_five_wrong_tries(client, email_on):
    phone, n, reg = _new_user(client)
    client.post("/api/auth/forgot", json={"identity": phone})
    code = _code_from(email_on[-1])
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post("/api/auth/reset-with-code", json={"identity": phone, "code": wrong, "password": "brand-new-1"})
    # Even the right code no longer works.
    assert client.post("/api/auth/reset-with-code", json={"identity": phone, "code": code, "password": "brand-new-1"}).status_code == 400


def test_unknown_account_looks_the_same_and_sends_nothing(client, email_on):
    r = client.post("/api/auth/forgot", json={"identity": "nobody-here@example.com"})
    assert r.status_code == 200 and r.json()["method"] == "email"
    assert email_on == []


def test_ask_team_instead_of_email(client, email_on):
    phone, n, reg = _new_user(client)
    r = client.post("/api/auth/forgot?team=true", json={"identity": phone})
    assert r.json()["method"] == "team" and email_on == []
    reqs = client.get("/api/admin/login-problems", headers=ADMIN).json()["requests"]
    assert any(q["user_id"] == reg["user"]["id"] for q in reqs)


def test_failed_email_goes_to_the_team(client, monkeypatch, email_on):
    from app import emailer

    async def broken(*a, **k):
        return False

    monkeypatch.setattr(emailer, "send_email", broken)
    phone, n, reg = _new_user(client)
    assert client.post("/api/auth/forgot", json={"identity": phone}).json()["method"] == "team"
    problems = client.get("/api/admin/login-problems", headers=ADMIN).json()
    assert any(e["outcome"] == "reset_code_failed" and e["user_id"] == reg["user"]["id"] for e in problems["failed"])


def test_email_status_and_test_email(client, email_on):
    st = client.get("/api/admin/email-status", headers=ADMIN).json()
    assert st["enabled"] is True and st["from"] == "noreply@example.com"
    assert client.post("/api/admin/email-test", headers=ADMIN, json={"to": "me@example.com"}).status_code == 200
    assert email_on[-1]["to"] == "me@example.com"
    assert client.post("/api/admin/email-test", json={"to": "me@example.com"}).status_code == 403


def test_without_email_it_uses_the_team(client):
    phone, n, reg = _new_user(client)
    assert client.post("/api/auth/forgot", json={"identity": phone}).json()["method"] == "team"
    assert client.get("/api/admin/email-status", headers=ADMIN).json()["enabled"] is False


def test_brevo_request_shape(monkeypatch):
    """The call to Brevo has the right address, key header and body."""
    import asyncio

    import httpx

    from app import emailer
    from app.config import get_settings
    cfg = get_settings()
    monkeypatch.setattr(cfg, "BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setattr(cfg, "EMAIL_FROM", "noreply@example.com")
    seen = {}

    def handler(request: httpx.Request):
        import json
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"messageId": "<1@brevo>"})

    real = httpx.AsyncClient
    monkeypatch.setattr(emailer.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    subject, text, body = emailer.code_email("Hawa", "123456", 15)
    assert asyncio.run(emailer.send_email("hawa@example.com", "Hawa Sesay", subject, text, body)) is True
    assert seen["url"] == "https://api.brevo.com/v3/smtp/email" and seen["key"] == "xkeysib-test"
    assert seen["body"]["sender"] == {"name": "SalonAutoZone", "email": "noreply@example.com"}
    assert seen["body"]["to"] == [{"email": "hawa@example.com", "name": "Hawa Sesay"}]
    assert "123456" in seen["body"]["subject"] and "123456" in seen["body"]["htmlContent"]

    monkeypatch.setattr(emailer.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})), **kw))
    assert asyncio.run(emailer.send_email("hawa@example.com", "Hawa", subject, text, body)) is False
