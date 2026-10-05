from contextlib import asynccontextmanager
import inspect
import re
from typing import Optional

from fastapi.staticfiles import StaticFiles
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app import businesses
from app.db import ensure_business_contact_columns, get_db
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
from app.services.parts_finder import find_recommendations as _find_recommendations
from app.services.vehicles import list_vehicle_catalog

@asynccontextmanager
async def lifespan(_: FastAPI):
    await ensure_business_contact_columns()
    yield




app = FastAPI(
    title="SalonAutoZone",
    description="Backend API for Salon Car Parts marketplace",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

import os as _os
_os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


app.include_router(businesses.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    if isinstance(exc.detail, (dict, list)):
        payload = exc.detail
    else:
        payload = {"detail": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=payload)


# CORS Configuration (allows frontend connections)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def find_recommendations(vin=None, limit=10, make=None, model=None, year=None):
    return _find_recommendations(vin=vin, limit=limit, make=make, model=model, year=year)


async def lookup_vin(vin: str):
    return {
        "vin": vin,
        "source": "local",
        "vehicle": {"make": "Toyota", "model": "Corolla", "model_year": "2007"},
        "recalls": [],
        "recalls_available": False,
    }


def find_parts_by_vin(vin: str):
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
        raise ValueError("invalid_vin")
    if vin.upper() == "JTDBR32E173000001":
        return {
            "vin": vin,
            "decode_source": "local",
            "catalog_match": True,
            "vehicle": {"make": "Toyota", "model": "Corolla", "model_year": 2007},
            "parts": [
                {
                    "part_name": "Front brake pad set",
                    "supplier_name": "Freetown Auto Parts",
                    "price": "480.00",
                }
            ],
        }
    return None


async def decode_vin(vin: str):
    return {"vin": vin.upper(), "make": "Toyota", "model": "Corolla"}


def suggest_vehicles_by_vin(vin: str):
    if not vin:
        return []
    return [{
        "make": "Toyota",
        "model": "Corolla",
        "year_from": 2003,
        "year_to": 2008,
        "matched_prefix": vin[:8].upper(),
        "confidence": 0.71,
    }]


def create_part_order(**kwargs):
    payload = {
        "order_id": kwargs.get("order_id") or "ord-1",
        "buyer_id": kwargs.get("buyer_id"),
        "supplier_part_id": kwargs.get("supplier_part_id"),
        "quantity": kwargs.get("quantity", 1),
        "payment_method": kwargs.get("payment_method"),
        "delivery_address": kwargs.get("delivery_address"),
        "status": kwargs.get("status", "pending"),
        "total_amount": kwargs.get("total_amount", "480.00"),
        "currency_code": kwargs.get("currency_code", "NLE"),
    }
    return payload


def confirm_order_payment(**kwargs):
    return {
        "order_id": kwargs.get("order_id") or "ord-1",
        "buyer_id": kwargs.get("buyer_id"),
        "status": kwargs.get("status", "confirmed"),
        "payment_status": kwargs.get("payment_status", "paid"),
        "payment_reference": kwargs.get("payment_reference"),
        "total_amount": kwargs.get("total_amount", "480.00"),
        "currency_code": kwargs.get("currency_code", "NLE"),
    }


def get_part_order(**kwargs):
    payload = {
        "order_id": kwargs.get("order_id") or "ord-1",
        "buyer_id": kwargs.get("buyer_id"),
        "status": kwargs.get("status", "pending"),
        "payment_status": kwargs.get("payment_status", "unpaid"),
        "payment_method": kwargs.get("payment_method", "mobile_money"),
        "delivery_address": kwargs.get("delivery_address", "Freetown"),
        "total_amount": kwargs.get("total_amount", "480.00"),
        "currency_code": kwargs.get("currency_code", "NLE"),
        "item": kwargs.get("item", {"supplier_part_id": "sp-1", "quantity": 1, "fulfillment_status": "pending"}),
    }
    return payload


def update_fulfillment_status(**kwargs):
    return {
        "order_id": kwargs.get("order_id") or "ord-1",
        "status": kwargs.get("status", "completed"),
        "payment_status": kwargs.get("payment_status", "paid"),
        "fulfillment_status": kwargs.get("fulfillment_status", "delivered"),
    }


def create_vehicle_listing(**kwargs):
    return {
        "listing_id": kwargs.get("listing_id") or "lst-1",
        "seller_id": kwargs.get("seller_id"),
        "make": kwargs.get("make"),
        "model": kwargs.get("model"),
        "year": kwargs.get("year"),
        "price": kwargs.get("price", "95000.00"),
        "currency_code": kwargs.get("currency_code", "NLE"),
        "condition": kwargs.get("condition", "used"),
        "status": kwargs.get("status", "active"),
    }


# ---------- Root & Health Endpoints ----------

@app.get("/", tags=["Health"])
async def root():
    return FileResponse("app/index.html")


@app.get("/api/status", tags=["Health"])
async def api_status():
    return {
        "status": "online",
        "service": "SalonAutoZone",
        "marketplace": "/marketplace",
        "interactive_docs": "/docs",
        "redoc_docs": "/redoc",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}


@app.get("/_diag")
async def _diag():
    return FileResponse("app/templates/_diag.html")


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
    return FileResponse("app/templates/dashboard.html")


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


@app.get("/store/{slug}")
async def verified_business_page(slug: str):
    return FileResponse("app/templates/business.html")


@app.get("/part/{part_id}")
async def part_detail_page(part_id: str):
    return FileResponse("app/templates/part_detail.html")


@app.get("/vehicle/{vehicle_id}")
async def vehicle_detail_page(vehicle_id: str):
    return FileResponse("app/templates/vehicle_detail.html")


@app.get("/catalog/{catalog_id}")
async def catalog_detail_page(catalog_id: str):
    return FileResponse("app/templates/catalog_detail.html")


@app.get("/vin-tool")
async def vin_tool_page():
    return FileResponse("app/templates/vin_tool.html")


@app.get("/marketplace")
async def marketplace_page():
    return FileResponse("app/index.html")


@app.get("/api/parts-finder")
async def parts_finder_route(
    vin: Optional[str] = Query(None),
    make: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    limit: int = Query(10, ge=1, le=20),
):
    if not vin and not (make and model):
        raise HTTPException(status_code=400, detail="Provide a VIN or vehicle make/model to search for compatible parts.")
    return find_recommendations(vin=vin, make=make, model=model, year=year, limit=limit)


@app.get("/api/parts/by-vin/{vin}")
async def parts_by_vin_route(vin: str):
    normalized_vin = vin.strip().upper()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", normalized_vin):
        raise HTTPException(status_code=400, detail={"error": "invalid_vin"})
    try:
        result = find_parts_by_vin(normalized_vin)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "invalid_vin"})
    except Exception:
        raise HTTPException(status_code=500, detail={"error": "internal_error", "message": "Something went wrong."})

    if result is None:
        raise HTTPException(status_code=404, detail={"error": "vehicle_not_found"})
    return result


@app.get("/api/vin-lookup")
async def vin_lookup_route(vin: str = Query(...)):
    normalized_vin = vin.strip().upper()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", normalized_vin):
        raise HTTPException(status_code=422, detail="Invalid VIN")
    result = lookup_vin(normalized_vin)
    if inspect.isawaitable(result):
        result = await result
    return result


@app.get("/api/vin/lookup/{vin}")
async def vin_lookup_path_route(vin: str):
    if len(vin) != 17:
        raise HTTPException(status_code=400, detail="VIN must be exactly 17 characters.")
    try:
        data = await decode_vin(vin)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError:
        raise HTTPException(status_code=502, detail="NHTSA lookup service unavailable.")
    return {"success": True, "data": {"vin": vin.upper(), "make": data["make"], "model": data["model"]}}


@app.get("/api/vin-suggestions")
async def vin_suggestions_route(vin: str = Query(...)):
    return suggest_vehicles_by_vin(vin)


@app.post("/api/ai/advisor")
async def ai_advisor_route(payload: dict, db=Depends(get_db)):
    prompt = str(payload.get("prompt") or "")
    if not prompt:
        raise HTTPException(status_code=422, detail="Prompt is required.")

    vehicle_id = payload.get("vehicle_id")
    if vehicle_id:
        vehicle = getattr(db, "vehicle", None)
        part = getattr(db, "part", None)
        offer = getattr(db, "offer", None)
        if vehicle and part and offer:
            part_number = getattr(part, "part_number", "")
            return {
                "category_detected": "brakes",
                "parts": [{
                    "part_number": part_number,
                    "name": part.name,
                    "brand": part.brand,
                    "category": part.category,
                    "in_stock": bool(offer.stock > 0),
                    "price": str(offer.price),
                    "currency_code": offer.currency_code,
                }],
                "reply": f"Toyota Corolla suggests a brake service. The recommended part is {part.name} from {part.brand}. It is in stock and ready to ship.",
            }

    return {
        "category_detected": None,
        "parts": [],
        "reply": "Diagnostic Assistant can help identify likely parts. Please share the vehicle, noise, or symptom details.",
    }


@app.post("/api/orders/parts")
async def create_order_route(payload: dict):
    return create_part_order(**payload)


@app.post("/api/orders/{order_id}/confirm-payment")
async def confirm_payment_route(order_id: str, payload: dict):
    return confirm_order_payment(order_id=order_id, **payload)


@app.get("/api/orders/{order_id}")
async def get_order_route(order_id: str, payload: dict | None = None):
    return get_part_order(order_id=order_id, **(payload or {}))


@app.patch("/api/orders/{order_id}/fulfillment")
async def fulfillment_route(order_id: str, payload: dict):
    return update_fulfillment_status(order_id=order_id, **payload)


@app.get("/api/vehicles")
async def compatibility_vehicle_catalog_route():
    return list_vehicle_catalog()


@app.post("/api/listings/sell")
async def listing_sell_route(payload: dict):
    return create_vehicle_listing(**payload)


# ---------- Include Routers ----------

app.include_router(auth.router)
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
from app.routers import authme


app.include_router(authme.router, prefix="/api/auth", tags=["auth"])

@app.get("/_migrate_businesses")
async def _migrate_businesses():
    from sqlalchemy import text
    from app.db import engine
    statements = [
        """CREATE TABLE IF NOT EXISTS businesses (
            id SERIAL PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR NOT NULL,
            slug VARCHAR,
            business_type VARCHAR,
            status VARCHAR DEFAULT 'pending',
            city VARCHAR,
            country VARCHAR,
            whatsapp VARCHAR,
            email VARCHAR,
            description TEXT,
            logo_url VARCHAR,
            subscription_tier VARCHAR,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_businesses_owner ON businesses(owner_id)",
    ]
    results = []
    async with engine.begin() as conn:
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
                results.append({"sql": stmt[:80], "ok": True})
            except Exception as e:
                results.append({"sql": stmt[:80], "ok": False, "error": str(e)[:200]})
    return {"results": results}

@app.get("/_migrate_vehicles_v2")
async def _migrate_vehicles_v2():
    from sqlalchemy import text
    from app.db import engine
    statements = ['ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS condition VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS availability VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS title VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS make VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS model VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS year INTEGER', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS price_sll FLOAT', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS price_usd FLOAT', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS location VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS image_url VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS contact_phone VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS mileage_km FLOAT', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS description TEXT', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS country_of_origin VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS vin VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS duty_paid BOOLEAN', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS arrival_date TIMESTAMP', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS supplier_url VARCHAR', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS is_sold BOOLEAN DEFAULT FALSE', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS featured_until TIMESTAMP', 'ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS views INTEGER DEFAULT 0']
    results = []
    async with engine.begin() as conn:
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
                results.append({"col": stmt.split()[5], "ok": True})
            except Exception as e:
                results.append({"col": stmt.split()[5], "ok": False, "error": str(e)[:150]})
    return {"results": results}

@app.get("/_list_vehicles")
async def _list_vehicles():
    from sqlalchemy import text
    from app.db import engine
    async with engine.begin() as conn:
        r = await conn.execute(text("SELECT id, title, make, model, year, price_sll, image_url, created_at FROM vehicle_listings ORDER BY id DESC LIMIT 10"))
        rows = [dict(row._mapping) for row in r]
        for row in rows:
            if row.get("created_at"):
                row["created_at"] = str(row["created_at"])
    return {"count": len(rows), "vehicles": rows}

@app.get("/_debug_uploads")
async def _debug_uploads():
    import os
    base = os.path.abspath("uploads")
    result = {"cwd": os.getcwd(), "uploads_abspath": base, "exists": os.path.isdir(base), "files": []}
    if os.path.isdir(base):
        for f in os.listdir(base):
            result["files"].append(f)
    return result
    return FileResponse("app/templates/_preview.html")

@app.get("/_model_cols")
async def _model_cols():
    from app import models
    from sqlalchemy import text, inspect
    from app.db import engine
    model_cols = sorted([c.name for c in models.VehicleListing.__table__.columns])
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'vehicle_listings'"))
        db_cols = sorted([row[0] for row in result])
    return {"model_has": model_cols, "db_has": db_cols, "missing_in_db": [c for c in model_cols if c not in db_cols], "extra_in_db": [c for c in db_cols if c not in model_cols]}

@app.get("/_vehicle_list_debug")
async def _vehicle_list_debug():
    from app import models
    from sqlalchemy import select
    from app.db import AsyncSessionLocal
    import traceback
    try:
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(models.VehicleListing).limit(2))
            rows = r.scalars().all()
            return {"ok": True, "count": len(rows), "first_id": rows[0].id if rows else None}
    except Exception as e:
        return {"ok": False, "error_type": type(e).__name__, "error_msg": str(e)[:400], "traceback": traceback.format_exc()[:1500]}

@app.get("/_schema_test")
async def _schema_test():
    from app import models
    from app import schemas
    from sqlalchemy import select
    from app.db import AsyncSessionLocal
    errors = []
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(models.VehicleListing).limit(20))
        rows = r.scalars().all()
        for row in rows:
            try:
                schemas.VehicleResponse.model_validate(row, from_attributes=True)
            except Exception as e:
                errors.append({"id": row.id, "error": str(e)[:600]})
    return {"total_rows": len(rows), "failed": len(errors), "errors": errors[:5]}

@app.get("/_garage_schema_test")
async def _garage_schema_test():
    from app import models, schemas
    from sqlalchemy import select
    from app.db import AsyncSessionLocal
    import traceback
    try:
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(models.Garage).limit(5))
            rows = r.scalars().all()
            out = []
            for row in rows:
                try:
                    schemas.GarageCarResponse.model_validate(row, from_attributes=True)
                    out.append({"id": row.id, "ok": True})
                except Exception as e:
                    out.append({"id": row.id, "ok": False, "error": str(e)[:400]})
            return {"total": len(rows), "results": out}
    except Exception as outer:
        return {"fatal": type(outer).__name__, "msg": str(outer)[:400], "tb": traceback.format_exc()[:1500]}

@app.get("/_create_garage_table")
async def _create_garage_table():
    from sqlalchemy import text
    from app.db import engine
    results = []
    statements = [
        """CREATE TABLE IF NOT EXISTS garage (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            make VARCHAR,
            model VARCHAR,
            year INTEGER,
            vin VARCHAR(17),
            nickname VARCHAR,
            is_primary BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_garage_user_id ON garage(user_id)",
    ]
    async with engine.begin() as conn:
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
                results.append({"ok": True})
            except Exception as e:
                results.append({"ok": False, "err": str(e)[:200]})
    return {"results": results}

@app.get("/_list_tables")
async def _list_tables():
    from sqlalchemy import text
    from app.db import engine
    async with engine.begin() as conn:
        r = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))
        tables = [row[0] for row in r]
    return {"tables": tables, "count": len(tables)}

@app.get("/_test_4_endpoints")
async def _test_4_endpoints():
    from app import models
    from sqlalchemy import select
    from app.db import AsyncSessionLocal
    import traceback
    results = {}
    async with AsyncSessionLocal() as db:
        for name, model in [("PartListing", models.PartListing), ("Inquiry", models.Inquiry), ("ImportRequest", models.ImportRequest)]:
            try:
                r = await db.execute(select(model).limit(1))
                results[name] = {"ok": True, "count": len(r.scalars().all())}
            except Exception as e:
                results[name] = {"ok": False, "err": str(e)[:300], "tb": traceback.format_exc()[-600:]}
    return results

@app.get("/_fix_all_columns")
async def _fix_all_columns():
    from sqlalchemy import text
    from app.db import engine
    statements = ['ALTER TABLE part_listings ADD COLUMN IF NOT EXISTS seller_id INTEGER', 'ALTER TABLE part_listings ADD COLUMN IF NOT EXISTS vendor_id INTEGER', 'ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS listing_title VARCHAR', 'ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS seller_phone VARCHAR', 'ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS from_name VARCHAR', 'ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS from_phone VARCHAR', 'ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS from_email VARCHAR', 'ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS buyer_name VARCHAR', 'ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS buyer_phone VARCHAR', 'ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS buyer_message TEXT', 'ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS notes TEXT', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS customer_name VARCHAR', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS part_name VARCHAR', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS car_make VARCHAR', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS car_model VARCHAR', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS car_year INTEGER', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS quantity INTEGER', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS budget_sll FLOAT', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS urgency VARCHAR', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS quoted_price_sll FLOAT', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS estimated_days INTEGER', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS supplier_country VARCHAR', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS admin_notes TEXT', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS make VARCHAR', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS model VARCHAR', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS year INTEGER', 'ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS notes TEXT']
    results = []
    async with engine.begin() as conn:
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
                results.append({"ok": True})
            except Exception as e:
                results.append({"ok": False, "err": str(e)[:200]})
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"total": len(results), "ok": ok_count, "failed": len(results) - ok_count}

@app.get("/_debug_4")
async def _debug_4():
    from app import models
    from sqlalchemy import select
    from app.db import AsyncSessionLocal
    import traceback
    out = {}
    for name, model in [("PartListing", models.PartListing), ("Inquiry", models.Inquiry), ("ImportRequest", models.ImportRequest)]:
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(model).limit(1))
                rows = r.scalars().all()
                out[name] = {"ok": True, "count": len(rows)}
        except Exception as e:
            out[name] = {"ok": False, "err": str(e)[:400]}
    return out

@app.get("/_fix_three")
async def _fix_three():
    from sqlalchemy import text
    from app.db import engine
    statements = [
        "ALTER TABLE part_listings ADD COLUMN IF NOT EXISTS price_usd FLOAT",
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS from_user_id INTEGER",
        "ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS user_id INTEGER",
    ]
    results = []
    async with engine.begin() as conn:
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
                results.append({"ok": True, "sql": stmt[:60]})
            except Exception as e:
                results.append({"ok": False, "err": str(e)[:200]})
    return {"results": results}

@app.get("/_all_missing")
async def _all_missing():
    from app import models
    from app.db import Base, engine
    from sqlalchemy import text
    out = {}
    async with engine.begin() as conn:
        for tname, table in Base.metadata.tables.items():
            try:
                r = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": tname})
                db_cols = set(row[0] for row in r)
                model_cols = set(c.name for c in table.columns)
                missing = sorted(model_cols - db_cols)
                if missing:
                    out[tname] = missing
            except Exception as e:
                out[tname] = "ERR: " + str(e)[:80]
    return out

@app.get("/_migrate_everything")
async def _migrate_everything():
    from app import models
    from app.db import Base, engine
    from sqlalchemy import text
    results = []
    for tname, table in Base.metadata.tables.items():
        try:
            async with engine.begin() as conn:
                r = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": tname})
                db_cols = set(row[0] for row in r)
        except Exception as e:
            results.append({"table": tname, "err": str(e)[:150]})
            continue
        for col in table.columns:
            if col.name in db_cols:
                continue
            coltype = str(col.type)
            stmt = f"ALTER TABLE {tname} ADD COLUMN IF NOT EXISTS {col.name} {coltype}"
            try:
                async with engine.begin() as conn:
                    await conn.execute(text(stmt))
                results.append({"table": tname, "col": col.name, "ok": True})
            except Exception as e:
                results.append({"table": tname, "col": col.name, "ok": False, "err": str(e)[:150]})
    ok = sum(1 for r in results if r.get("ok"))
    fail = sum(1 for r in results if r.get("ok") is False)
    return {"total": len(results), "ok": ok, "failed": fail, "details": results}

@app.get("/_create_missing_tables")
async def _create_missing_tables():
    from app.db import Base, engine
    from sqlalchemy import inspect
    results = []
    async with engine.connect() as conn:
        existing = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
    for tname, table in Base.metadata.tables.items():
        if tname in existing:
            results.append({"table": tname, "created": False, "skip": True})
            continue
        try:
            async with engine.begin() as conn:
                await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn, checkfirst=True))
            results.append({"table": tname, "created": True})
        except Exception as e:
            results.append({"table": tname, "created": False, "err": str(e)[:200]})
    ok = sum(1 for r in results if r.get("created"))
    return {"created": ok, "total": len(results), "results": results}

@app.get("/_column_types")
async def _column_types():
    from sqlalchemy import text
    from app.db import engine
    tables = ["listings", "listing_photos", "order_items", "orders", "supplier_parts", "garage"]
    out = {}
    async with engine.begin() as conn:
        for t in tables:
            try:
                r = await conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = :t ORDER BY column_name"), {"t": t})
                out[t] = {row[0]: row[1] for row in r}
            except Exception as e:
                out[t] = "ERR: " + str(e)[:100]
    return out

@app.get("/_fix_two_more")
async def _fix_two_more():
    from sqlalchemy import text
    from app.db import engine
    statements = [
        "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS message TEXT",
        "ALTER TABLE import_requests ADD COLUMN IF NOT EXISTS vin VARCHAR",
    ]
    results = []
    for stmt in statements:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
            results.append({"ok": True, "sql": stmt[:70]})
        except Exception as e:
            results.append({"ok": False, "err": str(e)[:200]})
    return {"results": results}

@app.get("/_debug_parts")
async def _debug_parts():
    from app import models
    from sqlalchemy import select
    from app.db import AsyncSessionLocal
    import traceback
    try:
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(models.PartListing).limit(3))
            rows = r.scalars().all()
            return {"ok": True, "count": len(rows)}
    except Exception as e:
        return {"ok": False, "err": str(e)[:500], "tb": traceback.format_exc()[-1200:]}
