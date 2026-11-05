"""Sitemap and robots for Google, HEAD on the home page, short links."""
import re
import uuid

from fastapi.testclient import TestClient

from app.main import app

ADMIN = {"X-Admin-Key": "test-admin-secret"}


def test_robots_and_sitemap():
    with TestClient(app) as c:
        n = int(uuid.uuid4().hex[:6], 16) % 900000 + 100000
        tok = c.post("/api/auth/register", json={"full_name": "Map Seller", "phone": f"+232 76 {n}",
                                                 "email": f"m{n}{uuid.uuid4().hex[:4]}@example.com", "password": "password123"}).json()["access_token"]
        auth = {"Authorization": "Bearer " + tok}
        part = c.post("/api/parts/", headers=auth, json={"name": "Sitemap brake pads", "category": "Brakes", "price_sll": 1000}).json()
        slug = c.post("/api/businesses/apply", headers=auth, json={"business_type": "store", "name": f"Map Shop {n}", "city": "Bo", "whatsapp": f"076{n}"}).json()["slug"]

        robots = c.get("/robots.txt", headers={"host": "salonautozone.com", "x-forwarded-proto": "https"})
        assert robots.status_code == 200 and "Disallow: /admin" in robots.text
        assert "Sitemap: https://salonautozone.com/sitemap.xml" in robots.text

        sm = c.get("/sitemap.xml", headers={"host": "salonautozone.com", "x-forwarded-proto": "https"})
        assert sm.status_code == 200 and sm.headers["content-type"].startswith("application/xml")
        locs = re.findall(r"<loc>([^<]+)</loc>", sm.text)
        assert "https://salonautozone.com/" in locs and "https://salonautozone.com/vin-tool" in locs
        assert f"https://salonautozone.com/part/{part['id']}" in locs
        assert f"https://salonautozone.com/store/{slug}" not in locs  # not verified yet

        bid = c.get("/api/businesses/mine", headers=auth).json()["business"]["id"]
        c.post(f"/api/businesses/admin/{bid}/status?status=verified", headers=ADMIN)
        assert f"/store/{slug}</loc>" in c.get("/sitemap.xml").text


def test_head_home_and_vin_link():
    with TestClient(app) as c:
        assert c.head("/").status_code == 200
        r = c.get("/vin", follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == "/vin-tool"


def test_login_page_has_moved_hint_and_home_has_launch_state():
    with TestClient(app) as c:
        assert "Had an account before 4 November?" in c.get("/login").text
        assert "Be among the first sellers" in c.get("/").text
