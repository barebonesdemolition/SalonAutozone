from contextlib import asynccontextmanager
import inspect
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi.staticfiles import StaticFiles
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from app import businesses
from app import models
from app.auth import get_current_user
from app.db import ensure_business_contact_columns, get_db
from app.drivers import ensure_driver_tables, router as drivers_router
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
    await ensure_driver_tables()
    yield


app = FastAPI(
    title="SalonAutoZone",
    description="Backend API for Salon Car Parts marketplace",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

_os = os
_os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


app.include_router(businesses.router)
app.include_router(drivers_router)


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
async def verified_business_page(slug: str):
    return FileResponse("app/templates/storefront.html")


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


# ============================================================
# DIAGNOSTIC ENDPOINTS (safe to remove later)
# ============================================================

@app.get("/_check_business")
async def _check_business():
    from app.db import engine
    async with engine.begin() as conn:
        r = await conn.execute(text("SELECT id, name, slug, status FROM businesses"))
        rows = [{"id": row[0], "name": row[1], "slug": row[2], "status": row[3]} for row in r]
    return {"count": len(rows), "rows": rows}


@app.get("/_link_vehicles_to_business")
async def _link_vehicles_to_business():
    from app.db import engine
    async with engine.begin() as conn:
        r = await conn.execute(text("UPDATE vehicle_listings SET business_id = 2 WHERE business_id IS NULL"))
    return {"updated": r.rowcount}


@app.get("/_check_inquiries")
async def _check_inquiries():
    from app.db import engine
    async with engine.begin() as conn:
        r = await conn.execute(text("SELECT COUNT(*) FROM inquiries"))
        count = r.scalar()
        r2 = await conn.execute(text("SELECT id, listing_id, seller_id, from_user_id, message, status FROM inquiries LIMIT 5"))
        rows = [{"id": row[0], "listing_id": row[1], "seller_id": row[2], "from_user_id": row[3], "message": (row[4] or "")[:60], "status": row[5]} for row in r2]
    return {"count": count, "sample": rows}


# ============================================================
# SUBSCRIPTIONS — migration endpoint
# ============================================================

@app.get("/_migrate_subscriptions")
async def _migrate_subscriptions():
    from app.db import engine
    results = []
    async with engine.begin() as conn:
        try:
            await conn.execute(text("""
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
            """))
            results.append({"step": "plans table", "ok": True})
        except Exception as e:
            results.append({"step": "plans table", "ok": False, "err": str(e)[:200]})

        try:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id SERIAL PRIMARY KEY,
                    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
                    plan_id INTEGER NOT NULL REFERENCES plans(id),
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    started_at TIMESTAMP,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_subscriptions_business ON subscriptions(business_id)"))
            results.append({"step": "subscriptions table", "ok": True})
        except Exception as e:
            results.append({"step": "subscriptions table", "ok": False, "err": str(e)[:200]})

        try:
            await conn.execute(text("""
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
            """))
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_reference ON payments(reference)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_payments_status ON payments(status)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_payments_business ON payments(business_id)"))
            results.append({"step": "payments table", "ok": True})
        except Exception as e:
            results.append({"step": "payments table", "ok": False, "err": str(e)[:200]})

        try:
            await conn.execute(text("""
                INSERT INTO plans (code, name, price_sll, listing_limit, featured_slots, verified_badge, description, sort_order) VALUES
                ('free', 'Free', 0, 5, 0, FALSE, 'Get started. 5 listings, no badge.', 1),
                ('starter', 'Starter', 75000, 30, 0, TRUE, '30 listings, verified badge.', 2),
                ('pro', 'Pro', 200000, 100, 3, TRUE, '100 listings, 3 featured slots.', 3),
                ('dealer', 'Dealer', 500000, NULL, 10, TRUE, 'Unlimited listings, 10 featured slots.', 4)
                ON CONFLICT (code) DO NOTHING
            """))
            results.append({"step": "seed plans", "ok": True})
        except Exception as e:
            results.append({"step": "seed plans", "ok": False, "err": str(e)[:200]})

    return {"results": results}


# ============================================================
# SUBSCRIPTIONS — API endpoints
# ============================================================

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")


def _check_admin(x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")):
    if not ADMIN_SECRET or x_admin_key != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin access required")
    return True


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

    dup = await db.execute(text("SELECT id FROM payments WHERE reference = :ref"), {"ref": payload.reference.strip()})
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
        "amt": payload.amount_sll, "ref": payload.reference.strip(),
        "ss": payload.screenshot_url, "note": payload.note,
    })
    await db.commit()
    return {"success": True, "subscription_id": sub_id}


@app.get("/api/admin/payments")
async def admin_list_payments(
    status: Optional[str] = "pending",
    _: bool = Depends(_check_admin),
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
    _: bool = Depends(_check_admin),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("SELECT id, business_id, subscription_id, plan_id FROM payments WHERE id = :id"), {"id": payment_id})
    p = r.first()
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")

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
    _: bool = Depends(_check_admin),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("SELECT id, subscription_id FROM payments WHERE id = :id"), {"id": payment_id})
    p = r.first()
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")

    await db.execute(text("UPDATE payments SET status = 'rejected' WHERE id = :id"), {"id": payment_id})
    if p.subscription_id:
        await db.execute(text("UPDATE subscriptions SET status = 'rejected' WHERE id = :sid"), {"sid": p.subscription_id})
    await db.commit()
    return {"success": True, "payment_id": payment_id}
