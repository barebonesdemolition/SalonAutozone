"""
Link previews for WhatsApp, Facebook and others.

Part and car pages are filled in by JavaScript, but link previews are built from
the raw HTML, so every shared link showed the same generic preview. These helpers
put the listing's title, price, description and photo into the page's <head>
before it is sent.
"""
from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path

SITE_NAME = "SalonAutoZone"
DEFAULT_DESCRIPTION = "Buy and sell car parts and cars across Sierra Leone. Find parts that fit your car."


@lru_cache(maxsize=8)
def _template(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def cloudinary_variant(url: str | None, transform: str) -> str | None:
    """Insert a Cloudinary transformation (e.g. 'w_1200,h_630,c_fill') into a Cloudinary URL."""
    if not url or "res.cloudinary.com" not in url or "/image/upload/" not in url:
        return url
    return url.replace("/image/upload/", f"/image/upload/{transform}/", 1)


def absolute(url: str | None, base_url: str) -> str | None:
    if not url:
        return None
    if url.startswith("https://") or url.startswith("http://"):
        return url
    if url.startswith("/") and not url.startswith("//"):
        return base_url.rstrip("/") + url
    return None


def _money(n) -> str:
    try:
        return f"SLL {float(n):,.0f}"
    except (TypeError, ValueError):
        return ""


def _clip(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render(template_path: str, *, title: str, description: str, url: str, image: str | None,
           kind: str = "website") -> str:
    """Return the template with title and Open Graph / Twitter tags for this page."""
    e = lambda s: html.escape(s or "", quote=True)  # noqa: E731
    page_title = f"{title} · {SITE_NAME}" if title else SITE_NAME
    tags = [
        f'<meta name="description" content="{e(description)}">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:type" content="{e(kind)}">',
        f'<meta property="og:title" content="{e(title or SITE_NAME)}">',
        f'<meta property="og:description" content="{e(description)}">',
        f'<meta property="og:url" content="{e(url)}">',
        f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">',
        f'<meta name="twitter:title" content="{e(title or SITE_NAME)}">',
        f'<meta name="twitter:description" content="{e(description)}">',
    ]
    if image:
        tags += [
            f'<meta property="og:image" content="{e(image)}">',
            f'<meta name="twitter:image" content="{e(image)}">',
        ]
    page = _template(template_path)
    page = re.sub(r"<title>.*?</title>", f"<title>{e(page_title)}</title>", page, count=1, flags=re.S)
    page = re.sub(r'\s*<meta name="description"[^>]*>', "", page, count=1)
    return page.replace("</head>", "\n".join(tags) + "\n</head>", 1)


def part_page(part, base_url: str, page_url: str) -> str:
    fits = " ".join(x for x in (part.compatible_make, part.compatible_model) if x)
    bits = [_money(part.price_sll), (part.condition or "").capitalize(), f"fits {fits}" if fits else "", part.location or ""]
    lead = ", ".join(b for b in bits if b)
    description = _clip(f"{lead}. {part.description or ''}".strip(". ")) if part.description else lead
    image = absolute(cloudinary_variant(part.image_url, "w_1200,h_630,c_fill,f_jpg,q_auto"), base_url)
    return render("app/templates/part_detail.html", title=part.name, description=description or DEFAULT_DESCRIPTION,
                  url=page_url, image=image, kind="product")


def vehicle_page(car, base_url: str, page_url: str) -> str:
    km = f"{car.mileage_km:,.0f} km" if car.mileage_km else ""
    bits = [_money(car.price_sll), str(car.year or ""), km, car.transmission or "", car.location or ""]
    lead = ", ".join(b for b in bits if b)
    description = _clip(f"{lead}. {car.description}") if car.description else lead
    image = absolute(cloudinary_variant(car.image_url, "w_1200,h_630,c_fill,f_jpg,q_auto"), base_url)
    return render("app/templates/vehicle_detail.html", title=car.title, description=description or DEFAULT_DESCRIPTION,
                  url=page_url, image=image, kind="product")


def home_page(base_url: str, page_url: str) -> str:
    return render("app/index.html", title="Car parts and cars in Sierra Leone", description=DEFAULT_DESCRIPTION,
                  url=page_url, image=absolute("/static/logo.png", base_url))
