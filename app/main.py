from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.routers import (
    auth, parts, listings, orders, vin, upload, ai_chat, search, imports,
    catalog, unified_search, inquiries, admin, identify, nhtsa, my_account,
    vehicles, garage
)
from app.db import engine, Base
from app.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy import text

        # ---------- One-time migrations ----------
        _migrations = [
            "ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0",
            "ALTER TABLE part_listings ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0",
            "ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE",
            "ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS featured_until TIMESTAMP NULL",
            "ALTER TABLE part_listings ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE",
            "ALTER TABLE part_listings ADD COLUMN IF NOT EXISTS featured_until TIMESTAMP NULL",
            """CREATE TABLE IF NOT EXISTS garages (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                make VARCHAR NOT NULL,
                model VARCHAR NOT NULL,
                year INTEGER NOT NULL,
                nickname VARCHAR,
                is_primary BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS saved_listings (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                listing_type VARCHAR NOT NULL,
                listing_id INTEGER NOT NULL,
                listing_title VARCHAR,
                listing_price_sll FLOAT,
                listing_image_url VARCHAR,
                notes TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
        ]
        for _sql in _migrations:
            try:
                await conn.execute(text(_sql))
            except Exception as _e:
                print(f"[migration-skip] {_sql[:60]}... ({_e})")

        print("[lifespan] Column migrations applied")

        # ---------- Indexes ----------
        _indexes = [
            "CREATE INDEX IF NOT EXISTS idx_vehicle_created_at ON vehicle_listings (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_vehicle_make_model ON vehicle_listings (make, model)",
            "CREATE INDEX IF NOT EXISTS idx_vehicle_location_created ON vehicle_listings (location, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_vehicle_seller ON vehicle_listings (seller_id)",
            "CREATE INDEX IF NOT EXISTS idx_vehicle_sold_created ON vehicle_listings (is_sold, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_vehicle_featured ON vehicle_listings (is_featured, created_at DESC)",

            "CREATE INDEX IF NOT EXISTS idx_part_created_at ON part_listings (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_part_category_created ON part_listings (category, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_part_make_model ON part_listings (compatible_make, compatible_model)",
            "CREATE INDEX IF NOT EXISTS idx_part_location_created ON part_listings (location, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_part_vendor ON part_listings (vendor_id)",
            "CREATE INDEX IF NOT EXISTS idx_part_condition ON part_listings (condition)",
            "CREATE INDEX IF NOT EXISTS idx_part_featured ON part_listings (is_featured, created_at DESC)",

            "CREATE INDEX IF NOT EXISTS idx_import_created_at ON import_requests (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_import_status_created ON import_requests (status, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_import_phone ON import_requests (customer_phone)",

            "CREATE INDEX IF NOT EXISTS idx_catalog_created_at ON supplier_catalog (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_catalog_category_created ON supplier_catalog (category, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_catalog_part_number ON supplier_catalog (part_number)",

            "CREATE INDEX IF NOT EXISTS idx_inquiry_created_at ON inquiries (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_inquiry_seller_created ON inquiries (seller_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_inquiry_listing ON inquiries (listing_type, listing_id)",
            "CREATE INDEX IF NOT EXISTS idx_inquiry_status ON inquiries (status)",

            "CREATE INDEX IF NOT EXISTS idx_order_created_at ON orders (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_order_buyer_created ON orders (buyer_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_order_status ON orders (status)",

            "CREATE INDEX IF NOT EXISTS idx_user_email ON users (email)",
            "CREATE INDEX IF NOT EXISTS idx_user_phone ON users (phone)",

            "CREATE INDEX IF NOT EXISTS idx_garage_user ON garages (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_garage_user_primary ON garages (user_id, is_primary)",

            "CREATE INDEX IF NOT EXISTS idx_saved_user ON saved_listings (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_saved_user_listing ON saved_listings (user_id, listing_type, listing_id)",
        ]
        created = 0
        for _sql in _indexes:
            try:
                await conn.execute(text(_sql))
                created += 1
            except Exception as _e:
                print(f"[index-skip] {_sql[:70]}... ({_e})")

        print(f"[lifespan] Indexes ensured: {created}/{len(_indexes)}")

    yield
    await engine.dispose()


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


# ==================== ROUTERS ====================
app.include_router(auth.router)
app.include_router(parts.router)
app.include_router(listings.router)
app.include_router(orders.router)
app.include_router(vin.router)
app.include_router(upload.router)
app.include_router(ai_chat.router)
app.include_router(search.router)
app.include_router(imports.router)
app.include_router(catalog.router)
app.include_router(unified_search.router)
app.include_router(inquiries.router)
app.include_router(admin.router)
app.include_router(identify.router)
app.include_router(nhtsa.router)
app.include_router(my_account.router)
app.include_router(vehicles.router)
app.include_router(garage.router)


# ==================== FRONTEND PAGES ====================

# 🏠 HOMEPAGE — serves the marketplace (hero + categories + results + featured)
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="vin_lookup.html",
        context={"app_name": settings.APP_NAME},
    )


# 🔍 VIN TOOL — the VIN decoder page
@app.get("/vin-tool", response_class=HTMLResponse)
async def vin_tool_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="vin_tool.html",
        context={"app_name": settings.APP_NAME},
    )


# 🔐 LOGIN
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"app_name": settings.APP_NAME},
    )


# 👋 WELCOME (legacy — kept as alias to /)
@app.get("/welcome", response_class=HTMLResponse)
async def welcome_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="vin_lookup.html",
        context={"app_name": settings.APP_NAME},
    )


# 📊 DASHBOARD → MY ACCOUNT redirect
@app.get("/dashboard")
async def dashboard_redirect():
    return RedirectResponse(url="/my-account", status_code=302)


# 👤 MY ACCOUNT
@app.get("/my-account", response_class=HTMLResponse)
async def my_account_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="my_account.html",
        context={"app_name": settings.APP_NAME},
    )


# 🚗 MY GARAGE
@app.get("/garage", response_class=HTMLResponse)
async def garage_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="garage.html",
        context={"app_name": settings.APP_NAME},
    )


# 🚙 VEHICLE DETAIL
@app.get("/vehicle/{vehicle_id}", response_class=HTMLResponse)
async def vehicle_detail(request: Request, vehicle_id: int):
    return templates.TemplateResponse(
        request=request,
        name="vehicle_detail.html",
        context={"app_name": settings.APP_NAME},
    )


# 🔧 PART DETAIL
@app.get("/part/{part_id}", response_class=HTMLResponse)
async def part_detail(request: Request, part_id: int):
    return templates.TemplateResponse(
        request=request,
        name="part_detail.html",
        context={"app_name": settings.APP_NAME},
    )


# 🛡️ ADMIN DASHBOARD (secret URL)
@app.get("/admin-panel-x9k2m7", response_class=HTMLResponse)
async def admin_dashboard_secret(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={"app_name": settings.APP_NAME},
    )


# 📦 ADMIN CATALOG
@app.get("/admin/catalog", response_class=HTMLResponse)
async def admin_catalog_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="admin_catalog.html",
        context={"app_name": settings.APP_NAME},
    )


# 🌍 CATALOG DETAIL
@app.get("/catalog/{part_id}", response_class=HTMLResponse)
async def catalog_detail_page(request: Request, part_id: int):
    return templates.TemplateResponse(
        request=request,
        name="catalog_detail.html",
        context={"app_name": settings.APP_NAME},
    )


# ==================== MIGRATION ENDPOINT ====================
@app.get("/admin/run-migration-xyz")
async def run_migration():
    from sqlalchemy import text

    migrations = [
        ("vehicle_listings.views",
         "ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0"),
        ("part_listings.views",
         "ALTER TABLE part_listings ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0"),
        ("vehicle_listings.is_featured",
         "ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE"),
        ("vehicle_listings.featured_until",
         "ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS featured_until TIMESTAMP NULL"),
        ("part_listings.is_featured",
         "ALTER TABLE part_listings ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE"),
        ("part_listings.featured_until",
         "ALTER TABLE part_listings ADD COLUMN IF NOT EXISTS featured_until TIMESTAMP NULL"),
        ("garages",
         """CREATE TABLE IF NOT EXISTS garages (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            make VARCHAR NOT NULL,
            model VARCHAR NOT NULL,
            year INTEGER NOT NULL,
            nickname VARCHAR,
            is_primary BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
         )"""),
        ("saved_listings",
         """CREATE TABLE IF NOT EXISTS saved_listings (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            listing_type VARCHAR NOT NULL,
            listing_id INTEGER NOT NULL,
            listing_title VARCHAR,
            listing_price_sll FLOAT,
            listing_image_url VARCHAR,
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
         )"""),
    ]

    results = []
    try:
        async with engine.begin() as conn:
            for label, sql in migrations:
                try:
                    await conn.execute(text(sql))
                    results.append(f"{label}: OK")
                except Exception as e:
                    results.append(f"{label}: {e}")
        return {"status": "success", "results": results}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ==================== HEALTH CHECK ====================
@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}
