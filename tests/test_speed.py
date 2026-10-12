"""Pages stay light on slow mobile data: compressed, cached, and no broken file links."""
import glob
import json
import os
import re

from fastapi.testclient import TestClient

from app.main import app

PAGES = glob.glob("app/templates/*.html") + ["app/index.html"]


def test_pages_and_assets_are_compressed_and_cached():
    with TestClient(app) as c:
        home = c.get("/", headers={"Accept-Encoding": "gzip"})
        assert home.headers.get("content-encoding") == "gzip"
        assert "cache-control" not in home.headers  # pages always fresh

        css = c.get("/static/ui.css?v=6", headers={"Accept-Encoding": "gzip"})
        assert css.headers.get("content-encoding") == "gzip"
        assert "max-age=604800" in css.headers["cache-control"]

        icon = c.get("/static/icon-64.png")
        assert icon.status_code == 200 and "max-age=3600" in icon.headers["cache-control"]

        api = c.get("/api/site/contact")
        assert "cache-control" not in api.headers or "max-age" not in api.headers["cache-control"]


def test_every_static_file_a_page_links_to_exists():
    missing = []
    for page in PAGES + ["app/static/site.js"]:
        text = open(page, encoding="utf-8").read()
        for ref in set(re.findall(r"/static/([\w./-]+?\.(?:css|js|png|webp|jpg|svg|webmanifest))", text)):
            if not os.path.exists(os.path.join("app/static", ref)):
                missing.append(f"{page}: /static/{ref}")
    assert not missing, missing


def test_header_uses_small_logo_and_manifest_icons_exist():
    site = open("app/static/site.js", encoding="utf-8").read()
    assert "/static/logo-80.webp" in site and "/static/logo.png" not in site
    assert os.path.getsize("app/static/logo-80.webp") < 10_000
    manifest = json.load(open("app/static/manifest.webmanifest"))
    for icon in manifest["icons"]:
        assert os.path.exists("app" + icon["src"])


def test_font_does_not_block_the_page():
    for page in PAGES:
        text = open(page, encoding="utf-8").read()
        for tag in re.findall(r"<link[^>]+fonts\.googleapis\.com/css2[^>]*>", text):
            inside_noscript = f"<noscript>{tag}</noscript>" in text
            assert inside_noscript or 'media="print"' in tag, f"{page} loads the font in a blocking way"
