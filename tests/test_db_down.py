"""When the database can't be reached the site starts anyway and shows a maintenance message;
CANONICAL_HOST sends old addresses to the main domain."""
import pytest
import sqlalchemy.exc as sa_exc
from fastapi.testclient import TestClient

from app import main
from app.db import is_db_down


def _down():
    return sa_exc.OperationalError("SELECT 1", {}, ConnectionRefusedError(111, "Connection refused"))


def test_is_db_down():
    assert is_db_down(_down())
    assert is_db_down(ConnectionRefusedError())
    assert is_db_down(TimeoutError())
    assert not is_db_down(sa_exc.ProgrammingError("SELECT x", {}, Exception("no such column")))
    assert not is_db_down(ValueError("bad"))


@pytest.fixture
def broken_db(monkeypatch):
    async def fail():
        raise _down()
    monkeypatch.setattr(main, "_setup_database", fail)

    async def no_retry():
        return None
    monkeypatch.setattr(main, "_retry_database_setup", no_retry)

    @main.app.get("/api/_test_db_down")
    async def _boom():
        raise _down()

    @main.app.get("/_test_page_down")
    async def _boom_page():
        raise _down()

    @main.app.get("/api/_test_real_bug")
    async def _bug():
        raise sa_exc.ProgrammingError("SELECT x", {}, Exception("no such column"))
    yield
    main.DB_STATE.update(ready=True, error=None)


def test_starts_and_shows_maintenance(broken_db):
    with TestClient(main.app, raise_server_exceptions=False) as c:
        assert main.DB_STATE["ready"] is False
        api = c.get("/api/_test_db_down")
        assert api.status_code == 503 and "maintenance" in api.json()["detail"]
        assert api.headers["retry-after"] == "60"
        page = c.get("/_test_page_down", headers={"Accept": "text/html"})
        assert page.status_code == 503 and "Back soon" in page.text
        assert c.get("/api/_test_real_bug").status_code == 500  # real bugs aren't hidden
        h = c.get("/health").json()
        assert h["status"] == "ok" and h["tables_ready"] is False


def test_health_reports_database():
    with TestClient(main.app) as c:
        h = c.get("/health").json()
        assert h == {"status": "ok", "database": "ok", "tables_ready": True}


def test_redirect_to_main_domain(monkeypatch):
    monkeypatch.setenv("CANONICAL_HOST", "saloncarparts.com")
    with TestClient(main.app) as c:
        r = c.get("/part/5?x=1", headers={"host": "saloncarparts.onrender.com"}, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == "https://saloncarparts.com/part/5?x=1"
        r = c.get("/", headers={"host": "www.saloncarparts.com"}, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == "https://saloncarparts.com/"
        assert c.get("/health", headers={"host": "saloncarparts.onrender.com"}).status_code == 200
        assert c.get("/api/site/contact", headers={"host": "saloncarparts.com"}).status_code == 200
        post = c.post("/api/auth/login", headers={"host": "saloncarparts.onrender.com"}, json={"phone": "x", "password": "y"}, follow_redirects=False)
        assert post.status_code != 301


def test_no_redirect_without_setting(monkeypatch):
    monkeypatch.delenv("CANONICAL_HOST", raising=False)
    with TestClient(main.app) as c:
        assert c.get("/api/site/contact", headers={"host": "saloncarparts.onrender.com"}).status_code == 200
