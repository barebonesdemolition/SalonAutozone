from contextlib import asynccontextmanager
import os
from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi.staticfiles import StaticFiles
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.future import select as _orm_select

from app import share_meta

from app import businesses
from app import models
from app.auth import get_current_user
from app.stores import ensure_store_tables, router as stores_router
from app.accounts import admin_router as accounts_admin_router, ensure_account_tables, router as accounts_router
from app.db import drop_wrong_listing_foreign_keys, engine, ensure_core_tables, ensure_business_contact_columns, ensure_vehicle_listing_columns, get_db
from app.routers.admin import require_admin
from app.db import is_db_down
import logging as _logging

_log = _logging.getLogger("salonautozone")
# --- drivers (must exist as app/drivers.py or the app will not boot) ---
from app.drivers import ensure_driver_tables, router as drivers_router
from app.shop import ensure_delivery_tables, router as shop_router
from app.payments import ensure_payment_tables, router as payments_router
from app.photos import ensure_photo_tables, router as photos_router
from app.sellers import ensure_review_tables, router as sellers_router
# ---------------------------------------------------------------------
from app.routers import (
    admin,
    ai_chat,
    auth,
    catalog,
    garage,
    identify,
    imports as imports_router,
    inquiries,
    my_account,
    nhtsa,
    parts,
    search,
    unified_search,
    upload,
    vehicles,
    vin_lookup,
)


SUBSCRIPTION_DDL = [
    """
    CREATE TABLE IF NOT EXISTS plans (
        id SERIAL PRIMARY KEY,
        code VARCHAR NOT NULL UNIQUE,
        name VARCHAR NOT NULL,
        price_sll INTEGER NOT NULL,
        listing_limit INTEGER,
        featured_slots INTEGER NOT NULL DEFAULT 0,
        verified_badge BOOLEAN NOT NULL DEFAULT FALSE,
        description TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        id SERIAL PRIMARY KEY,
        business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        plan_id INTEGER NOT NULL REFERENCES plans(id),
        status VARCHAR NOT NULL DEFAULT 'pending',
        started_at TIMESTAMP,
        expires_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_subscriptions_business ON subscriptions(business_id)",
    """
    CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY,
        business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
        plan_id INTEGER NOT NULL REFERENCES plans(id),
        method VARCHAR NOT NULL DEFAULT 'orange_money',
        amount_sll INTEGER NOT NULL,
        reference VARCHAR NOT NULL,
        screenshot_url VARCHAR,
        status VARCHAR NOT NULL DEFAULT 'pending',
        note TEXT,
        confirmed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        confirmed_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_reference ON payments(reference)",
    "CREATE INDEX IF NOT EXISTS ix_payments_status ON payments(status)",
    "CREATE INDEX IF NOT EXISTS ix_payments_business ON payments(business_id)",
    """
    INSERT INTO plans (code, name, price_sll, listing_limit, featured_slots, verified_badge, description, sort_order) VALUES
    ('free', 'Free', 0, 5, 0, FALSE, 'Get started. 5 listings, no badge.', 1),
    ('starter', 'Starter', 75000, 30, 0, TRUE, '30 listings, verified badge.', 2),
    ('pro', 'Pro', 200000, 100, 3, TRUE, '100 listings, 3 featured slots.', 3),
    ('dealer', 'Dealer', 500000, NULL, 10, TRUE, 'Unlimited listings, 10 featured slots.', 4)
    ON CONFLICT (code) DO NOTHING
    """,
]


async def ensure_subscription_tables():
    """Create the plans/subscriptions/payments tables (Postgres only).

    This replaces the old public /_migrate_subscriptions URL, which anyone could call.
    """
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        exists = await conn.execute(text("SELECT to_regclass('public.businesses')"))
        if exists.scalar() is None:
            return
        for statement in SUBSCRIPTION_DDL:
            await conn.execute(text(statement))


async def _setup_database():
    await ensure_core_tables()
    await ensure_account_tables()
    await ensure_business_contact_columns()
    await ensure_store_tables()
    await ensure_vehicle_listing_columns()
    await drop_wrong_listing_foreign_keys()
    await ensure_subscription_tables()
    await ensure_driver_tables()
    await ensure_delivery_tables()
    await ensure_payment_tables()
    await ensure_photo_tables()
    await ensure_review_tables()


DB_STATE = {"ready": False, "error": None}


async def _retry_database_setup():
    """Keep trying until the database answers, then create the tables."""
    import asyncio
    delay = 30
    while not DB_STATE["ready"]:
        await asyncio.sleep(delay)
        try:
            await asyncio.wait_for(_setup_database(), timeout=60)
            DB_STATE.update(ready=True, error=None)
            _log.warning("Database is reachable again; tables are ready.")
        except Exception as exc:  # noqa: BLE001
            DB_STATE["error"] = type(exc).__name__
            delay = min(delay * 2, 300)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # If the database is down (e.g. a suspended free database), still start, so the
    # site can show a maintenance message instead of failing every deploy.
    import asyncio
    retry = None
    try:
        await asyncio.wait_for(_setup_database(), timeout=90)
        DB_STATE.update(ready=True, error=None)
    except Exception as exc:  # noqa: BLE001
        if not is_db_down(exc):
            raise
        DB_STATE.update(ready=False, error=type(exc).__name__)
        _log.error("Database unreachable at start-up (%s). Showing the maintenance message and retrying.", type(exc).__name__)
        retry = asyncio.create_task(_retry_database_setup())
    yield
    if retry:
        retry.cancel()


app = FastAPI(
    title="SalonAutoZone",
    description="Backend API for Salon Car Parts marketplace",
    version="1.0.0",
    lifespan=lifespan,
)

# Most buyers are on slow, metered mobile data: compress text responses and let
# phones keep static files instead of downloading them on every page.
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=800)


@app.middleware("http")
async def redirect_to_main_domain(request: Request, call_next):
    """With CANONICAL_HOST set (e.g. saloncarparts.com), send visitors on the old
    onrender.com address or on www. to the main address, keeping the path."""
    canonical = os.getenv("CANONICAL_HOST", "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    if canonical and request.method in ("GET", "HEAD") and request.url.path != "/health":
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].lower()
        if host and host != canonical and (host.endswith(".onrender.com") or host == "www." + canonical):
            target = f"https://{canonical}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(target, status_code=301)
    return await call_next(request)


@app.middleware("http")
async def cache_static_files(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if response.status_code == 200 and "cache-control" not in response.headers:
        if path.startswith("/uploads/"):
            # Upload names are random and never reused.
            response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        elif path.startswith("/static/"):
            # Pages link to CSS/JS with ?v=N, bumped when the file changes.
            versioned = "v=" in (request.url.query or "")
            response.headers["Cache-Control"] = "public, max-age=604800" if versioned else "public, max-age=3600"
    return response


app.mount("/static", StaticFiles(directory="app/static"), name="static")

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


app.include_router(stores_router)
app.include_router(businesses.router)
app.include_router(drivers_router)
app.include_router(shop_router)
app.include_router(payments_router)
app.include_router(photos_router)
app.include_router(sellers_router)


MAINTENANCE_TEXT = "SalonAutoZone is down for a short maintenance. Please try again in a little while."
MAINTENANCE_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Back soon · SalonAutoZone</title>
<meta http-equiv="refresh" content="60"><link rel="icon" href="/static/icon-64.png">
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0e2a2b;
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#fff;text-align:center;padding:24px}
.c{max-width:420px}img{width:80px;height:80px;border-radius:50%}h1{font-size:28px;margin:18px 0 8px}
p{color:#b9cbc9;font-size:17px;line-height:1.5}</style></head><body><div class="c">
<img src="/static/logo-80.webp" alt=""><h1>Back soon</h1>
<p>SalonAutoZone is down for a short maintenance. Please try again in a little while.</p>
<p style="font-size:14px">This page checks again every minute.</p></div></body></html>"""


async def database_down_handler(request: Request, exc: Exception):
    if not is_db_down(exc):
        raise exc
    _log.error("Database unreachable during %s %s: %s", request.method, request.url.path, type(exc).__name__)
    if request.url.path.startswith("/api/") or "text/html" not in request.headers.get("accept", ""):
        return JSONResponse(status_code=503, content={"detail": MAINTENANCE_TEXT}, headers={"Retry-After": "60"})
    return HTMLResponse(MAINTENANCE_HTML, status_code=503, headers={"Retry-After": "60"})


import sqlalchemy.exc as _sa_exc  # noqa: E402

for _exc_type in (_sa_exc.DBAPIError, ConnectionError, TimeoutError):
    app.add_exception_handler(_exc_type, database_down_handler)


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    if isinstance(exc.detail, (dict, list)):
        payload = exc.detail
    else:
        payload = {"detail": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=payload)


# CORS Configuration (allows frontend connections)
# The site's own pages are served from this app, so they need no CORS at all.
# List any other front-ends (e.g. a mobile web app) in ALLOWED_ORIGINS, comma-separated.
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
    )


def _public_base(request: Request) -> str:
    """The site's public address (https on Render, which sits behind a proxy)."""
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


# ---------- Root & Health Endpoints ----------

@app.api_route("/", methods=["GET", "HEAD"], tags=["Health"])
async def root(request: Request):
    base = _public_base(request)
    return HTMLResponse(share_meta.home_page(base, base + "/"))


@app.get("/api/status", tags=["Health"])
async def api_status():
    return {
        "status": "online",
        "service": "SalonAutoZone",
        "marketplace": "/marketplace",
        "interactive_docs": "/docs",
        "redoc_docs": "/redoc",
    }


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt(request: Request):
    from fastapi.responses import PlainTextResponse
    base = _public_base(request)
    return PlainTextResponse(
        "User-agent: *\n"
        "Disallow: /admin\nDisallow: /api/\nDisallow: /my-account\nDisallow: /account/\n"
        "Disallow: /business/\nDisallow: /cart\nDisallow: /checkout\nDisallow: /orders\n"
        "Disallow: /reset-password\nDisallow: /forgot-password\nDisallow: /drive\n"
        f"\nSitemap: {base}/sitemap.xml\n",
        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml(request: Request, db: AsyncSession = Depends(get_db)):
    """Every public page Google should know about: home, sections, listings, verified businesses."""
    from xml.sax.saxutils import escape
    from fastapi.responses import Response
    base = _public_base(request)
    urls = [(f"{base}/", None), (f"{base}/vin-tool", None), (f"{base}/businesses", None),
            (f"{base}/plans", None), (f"{base}/become-a-seller", None)]
    parts = (await db.execute(
        _orm_select(models.PartListing.id, models.PartListing.created_at)
        .where(models.PartListing.stock_quantity > 0)
        .order_by(models.PartListing.id.desc()).limit(20000))).all()
    urls += [(f"{base}/part/{i}", c) for i, c in parts]
    cars = (await db.execute(
        _orm_select(models.VehicleListing.id, models.VehicleListing.created_at)
        .where(models.VehicleListing.is_sold.is_(False))
        .order_by(models.VehicleListing.id.desc()).limit(20000))).all()
    urls += [(f"{base}/vehicle/{i}", c) for i, c in cars]
    shops = (await db.execute(
        _orm_select(models.Business.slug, models.Business.created_at)
        .where(models.Business.status == "verified"))).all()
    urls += [(f"{base}/store/{slug}", c) for slug, c in shops]
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, when in urls:
        body.append(f"<url><loc>{escape(loc)}</loc>" + (f"<lastmod>{when.date().isoformat()}</lastmod>" if when else "") + "</url>")
    body.append("</urlset>")
    return Response("\n".join(body), media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/health", tags=["Health"])
async def health_check():
    """Always 200 for the host's health checks; 'database' says whether data is reachable."""
    import asyncio
    db = "ok"
    try:
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=10)
    except Exception as exc:  # noqa: BLE001
        db = "unreachable" if is_db_down(exc) or isinstance(exc, asyncio.TimeoutError) else "error"
    return {"status": "ok", "database": db, "tables_ready": DB_STATE["ready"]}


@app.get("/login")
async def login_page():
    return FileResponse("app/templates/login.html")


@app.get("/sell")
async def sell_page():
    return FileResponse("app/templates/sell.html")


@app.get("/my-account")
async def my_account_page():
    return FileResponse("app/templates/my_account.html")


@app.get("/garage")
async def garage_page():
    return FileResponse("app/templates/garage.html")


@app.get("/dashboard")
async def legacy_dashboard_page():
    # The seller dashboard is now the "My listings" tab of the account page.
    return RedirectResponse("/my-account?tab=listings")


@app.get("/admin/login")
async def admin_login_page():
    return FileResponse("app/templates/admin_login.html")


@app.get("/admin")
async def admin_page():
    return RedirectResponse("/admin/login")


@app.get("/admin/dashboard")
async def admin_dashboard_page():
    return FileResponse("app/templates/admin_dashboard.html")


@app.get("/admin/catalog")
async def admin_catalog_page():
    return FileResponse("app/templates/admin_catalog.html")


@app.get("/become-a-seller")
async def become_a_seller_page():
    return FileResponse("app/templates/business.html")


@app.get("/businesses")
async def businesses_page():
    return FileResponse("app/templates/businesses.html")


@app.get("/plans")
async def plans_page():
    return FileResponse("app/templates/plans.html")


@app.get("/my-subscription")
async def my_subscription_page():
    return FileResponse("app/templates/my_subscription.html")


@app.get("/store/{slug}")
async def verified_business_page(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    biz = (await db.execute(_orm_select(models.Business).where(models.Business.slug == slug))).scalars().first()
    if not biz or biz.status != "verified":
        return FileResponse("app/templates/storefront.html")  # the page shows "not found" itself
    base = _public_base(request)
    return HTMLResponse(share_meta.business_page(biz, base, f"{base}/store/{slug}"))


@app.get("/part/{part_id}")
async def part_detail_page(part_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    part = None
    if part_id.isdigit():
        part = (await db.execute(_orm_select(models.PartListing).where(models.PartListing.id == int(part_id)))).scalars().first()
    if part is None:
        return FileResponse("app/templates/part_detail.html")  # the page shows "not found" itself
    base = _public_base(request)
    return HTMLResponse(share_meta.part_page(part, base, f"{base}/part/{part.id}"))


@app.get("/seller/{seller_id}")
async def seller_page(seller_id: str):
    return FileResponse("app/templates/seller.html")


@app.get("/vehicle/{vehicle_id}")
async def vehicle_detail_page(vehicle_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    car = None
    if vehicle_id.isdigit():
        car = (await db.execute(_orm_select(models.VehicleListing).where(models.VehicleListing.id == int(vehicle_id)))).scalars().first()
    if car is None:
        return FileResponse("app/templates/vehicle_detail.html")
    base = _public_base(request)
    return HTMLResponse(share_meta.vehicle_page(car, base, f"{base}/vehicle/{car.id}"))


@app.get("/catalog/{catalog_id}")
async def catalog_detail_page(catalog_id: str):
    return FileResponse("app/templates/catalog_detail.html")


@app.get("/vin")
async def vin_short_link():
    return RedirectResponse("/vin-tool", status_code=301)


@app.get("/vin-tool")
async def vin_tool_page():
    return FileResponse("app/templates/vin_tool.html")


@app.get("/marketplace")
async def marketplace_page():
    return FileResponse("app/index.html")


# ---------- Include Routers ----------

app.include_router(auth.router)
app.include_router(accounts_router)
app.include_router(accounts_admin_router())
app.include_router(admin.router)
app.include_router(my_account.router)
app.include_router(parts.router)
app.include_router(vehicles.router)
app.include_router(vin_lookup.router)
app.include_router(nhtsa.router)
app.include_router(upload.router)
app.include_router(search.router)
app.include_router(ai_chat.router)
app.include_router(garage.router)
app.include_router(identify.router)
app.include_router(imports_router.router)
app.include_router(inquiries.router)
app.include_router(catalog.router)
app.include_router(unified_search.router)

# ============================================================
# SUBSCRIPTIONS - API endpoints
# ============================================================


@app.get("/api/site/contact")
async def site_contact():
    """Public contact details for the platform (support WhatsApp, payment number)."""
    from app.config import get_settings
    cfg = get_settings()
    digits = lambda v: "".join(ch for ch in (v or "") if ch.isdigit())  # noqa: E731
    return {"whatsapp": digits(cfg.ADMIN_WHATSAPP) or None, "orange_money": (cfg.ORANGE_MONEY_NUMBER or None)}


@app.get("/api/plans")
async def list_plans(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text(
        "SELECT id, code, name, price_sll, listing_limit, featured_slots, verified_badge, description, sort_order "
        "FROM plans WHERE active = TRUE ORDER BY sort_order ASC"
    ))
    rows = [dict(r._mapping) for r in result]
    return {"plans": rows}


async def _get_my_business(user: models.User, db: AsyncSession):
    from sqlalchemy.future import select as _select
    result = await db.execute(_select(models.Business).where(models.Business.owner_id == user.id))
    return result.scalars().first()


@app.get("/api/subscriptions/my")
async def my_subscription(
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    biz = await _get_my_business(user, db)
    if not biz:
        return {"business": None, "subscription": None, "pending_payments": []}

    sub = await db.execute(text(
        "SELECT s.id, s.status, s.started_at, s.expires_at, "
        "p.code AS plan_code, p.name AS plan_name, p.price_sll, "
        "p.listing_limit, p.featured_slots, p.verified_badge "
        "FROM subscriptions s JOIN plans p ON p.id = s.plan_id "
        "WHERE s.business_id = :bid AND s.status IN ('active', 'pending') "
        "ORDER BY s.created_at DESC LIMIT 1"
    ), {"bid": biz.id})
    sub_row = sub.first()

    pending = await db.execute(text(
        "SELECT id, plan_id, amount_sll, reference, status, created_at "
        "FROM payments WHERE business_id = :bid ORDER BY created_at DESC LIMIT 10"
    ), {"bid": biz.id})
    pending_rows = [dict(r._mapping) for r in pending]

    return {
        "business": {"id": biz.id, "name": biz.name, "slug": biz.slug, "status": biz.status},
        "subscription": dict(sub_row._mapping) if sub_row else None,
        "pending_payments": pending_rows,
    }


class PaymentSubmit(BaseModel):
    plan_id: int
    amount_sll: int
    reference: str
    screenshot_url: Optional[str] = None
    note: Optional[str] = None


@app.post("/api/subscriptions/pay")
async def submit_payment(
    payload: PaymentSubmit,
    user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    biz = await _get_my_business(user, db)
    if not biz:
        raise HTTPException(status_code=400, detail="You must have a business account to subscribe")

    reference = payload.reference.strip()
    if not reference:
        raise HTTPException(status_code=422, detail="Enter the payment reference from your mobile money receipt")

    plan = (await db.execute(
        text("SELECT id, price_sll FROM plans WHERE id = :pid AND active = TRUE"), {"pid": payload.plan_id}
    )).first()
    if not plan:
        raise HTTPException(status_code=404, detail="That plan is not available")
    if plan.price_sll <= 0:
        raise HTTPException(status_code=400, detail="The free plan does not need a payment")
    if payload.amount_sll != plan.price_sll:
        raise HTTPException(
            status_code=400,
            detail=f"The amount for this plan is {plan.price_sll:,} SLL. Please pay and enter that exact amount.",
        )

    dup = await db.execute(text("SELECT id FROM payments WHERE reference = :ref"), {"ref": reference})
    if dup.first():
        raise HTTPException(status_code=400, detail="This payment reference has already been submitted")

    sub = await db.execute(text(
        "INSERT INTO subscriptions (business_id, plan_id, status) VALUES (:bid, :pid, 'pending') RETURNING id"
    ), {"bid": biz.id, "pid": payload.plan_id})
    sub_id = sub.scalar()

    await db.execute(text(
        "INSERT INTO payments (business_id, subscription_id, plan_id, amount_sll, reference, screenshot_url, note, status) "
        "VALUES (:bid, :sid, :pid, :amt, :ref, :ss, :note, 'pending')"
    ), {
        "bid": biz.id, "sid": sub_id, "pid": payload.plan_id,
        "amt": plan.price_sll, "ref": reference,
        "ss": payload.screenshot_url, "note": payload.note,
    })
    await db.commit()
    return {"success": True, "subscription_id": sub_id}


@app.get("/api/admin/payments")
async def admin_list_payments(
    status: Optional[str] = "pending",
    _: bool = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    q = "SELECT p.id, p.business_id, p.plan_id, p.amount_sll, p.reference, p.screenshot_url, p.status, p.note, p.created_at, " \
        "b.name AS business_name, b.slug AS business_slug, pl.name AS plan_name " \
        "FROM payments p JOIN businesses b ON b.id = p.business_id JOIN plans pl ON pl.id = p.plan_id "
    params = {}
    if status:
        q += "WHERE p.status = :st "
        params["st"] = status
    q += "ORDER BY p.created_at DESC LIMIT 100"
    result = await db.execute(text(q), params)
    rows = [dict(r._mapping) for r in result]
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"])
    return {"count": len(rows), "payments": rows}


@app.post("/api/admin/payments/{payment_id}/confirm")
async def admin_confirm_payment(
    payment_id: int,
    _: bool = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("SELECT id, business_id, subscription_id, plan_id, status FROM payments WHERE id = :id"), {"id": payment_id})
    p = r.first()
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")
    if p.status != "pending":
        raise HTTPException(status_code=409, detail=f"This payment is already {p.status}")

    now = datetime.utcnow()
    expires = now + timedelta(days=30)

    await db.execute(text(
        "UPDATE payments SET status = 'confirmed', confirmed_at = :now WHERE id = :id"
    ), {"id": payment_id, "now": now})

    if p.subscription_id:
        await db.execute(text(
            "UPDATE subscriptions SET status = 'active', started_at = :now, expires_at = :exp WHERE id = :sid"
        ), {"sid": p.subscription_id, "now": now, "exp": expires})

    await db.execute(text(
        "UPDATE businesses SET status = 'verified' WHERE id = :bid AND status = 'pending'"
    ), {"bid": p.business_id})

    await db.commit()
    return {"success": True, "payment_id": payment_id, "expires_at": str(expires)}


@app.post("/api/admin/payments/{payment_id}/reject")
async def admin_reject_payment(
    payment_id: int,
    _: bool = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("SELECT id, subscription_id, status FROM payments WHERE id = :id"), {"id": payment_id})
    p = r.first()
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")
    if p.status != "pending":
        raise HTTPException(status_code=409, detail=f"This payment is already {p.status}")

    await db.execute(text("UPDATE payments SET status = 'rejected' WHERE id = :id"), {"id": payment_id})
    if p.subscription_id:
        await db.execute(text("UPDATE subscriptions SET status = 'rejected' WHERE id = :sid"), {"sid": p.subscription_id})
    await db.commit()
    return {"success": True, "payment_id": payment_id}

# ---------------------------------------------------------------

@app.get("/checkout")
async def checkout_page():
    return FileResponse("app/templates/checkout.html")

@app.get("/cart")
async def cart_page():
    return FileResponse("app/templates/cart.html")

@app.get("/orders")
async def orders_page():
    return FileResponse("app/templates/orders.html")

@app.get("/track")
async def track_page():
    return FileResponse("app/templates/orders.html")

@app.get("/business/stock")
async def stock_scan_page():
    return FileResponse("app/templates/stock_scan.html")


@app.get("/business/page")
async def business_page_editor():
    return FileResponse("app/templates/business_page.html")


@app.get("/receipt")
async def receipt_page():
    return FileResponse("app/templates/receipt.html")


@app.get("/pay/{order_id}")
async def pay_page(order_id: int):
    return FileResponse("app/templates/pay.html", headers={"Cache-Control": "no-store"})


@app.get("/forgot-password")
async def forgot_password_page():
    return FileResponse("app/templates/forgot_password.html")


@app.get("/reset-password")
async def reset_password_page():
    return FileResponse("app/templates/reset_password.html")


@app.get("/account/password")
async def change_password_page():
    return FileResponse("app/templates/change_password.html")


@app.get("/driver")
async def driver_redirect():
    return RedirectResponse("/drive", status_code=301)


@app.get("/drive")
async def drive_page():
    return FileResponse("app/templates/driver.html")
