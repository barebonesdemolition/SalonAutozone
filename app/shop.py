"""Orders with delivery: place -> pay -> ready -> driver job -> delivered."""
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table,
    func, insert, select, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_user
from app.routers.admin import admin_actor
from app.db import engine, get_db
from app.drivers import drivers as drivers_t

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/shop", tags=["Orders & delivery"])

MAX_CODE_ATTEMPTS = 5
SIZE_CLASSES = {"small", "bulky"}
# Matched against the part's NAME only. Matching the category made every
# "Engine" or "Transmission" part (spark plugs, filters, oil) go by van.
BULKY_WORDS = (
    "engine block","engine assembly","complete engine","whole engine","gearbox","transmission assembly",
    "bumper","door","bonnet","hood","fender",
    "windscreen","windshield","seat","radiator","axle","tyre","tire","wheel",
    "rim","exhaust","muffler","dashboard","tailgate","body panel","fuel tank",
    "differential","driveshaft","sunroof",
)

_md = MetaData()
Table("users", _md, Column("id", Integer, primary_key=True))
Table("part_listings", _md, Column("id", Integer, primary_key=True))
Table("delivery_drivers", _md, Column("id", Integer, primary_key=True))

orders = Table("shop_orders", _md,
    Column("id", Integer, primary_key=True),
    Column("code", String, nullable=False, unique=True),
    Column("buyer_id", Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True),
    Column("seller_id", Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True),
    Column("status", String, nullable=False, server_default="awaiting_payment", index=True),
    Column("delivery_address", String, nullable=False),
    Column("delivery_area", String, nullable=False),
    Column("pickup_area", String, nullable=True),
    Column("buyer_phone", String, nullable=False),
    Column("vehicle_class", String, nullable=False),
    Column("parts_total_sll", Float, nullable=False),
    Column("delivery_fee_sll", Float, nullable=False),
    Column("platform_fee_sll", Float, nullable=False),
    Column("driver_payout_sll", Float, nullable=False),
    Column("delivery_code", String, nullable=False),
    Column("code_attempts", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow),
)
items_t = Table("shop_order_items", _md,
    Column("id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("shop_orders.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("part_id", Integer, ForeignKey("part_listings.id", ondelete="SET NULL"), nullable=True),
    Column("title", String, nullable=False),
    Column("unit_price_sll", Float, nullable=False),
    Column("qty", Integer, nullable=False),
    Column("size_class", String, nullable=False),
)
events_t = Table("shop_order_events", _md,
    Column("id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("shop_orders.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("status", String, nullable=False),
    Column("note", String, nullable=True),
    Column("actor_id", Integer, nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
)
jobs = Table("delivery_jobs", _md,
    Column("id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("shop_orders.id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("status", String, nullable=False, server_default="open", index=True),
    Column("vehicle_class", String, nullable=False),
    Column("driver_id", Integer, ForeignKey("delivery_drivers.id", ondelete="SET NULL"), nullable=True, index=True),
    Column("fee_sll", Float, nullable=False),
    Column("payout_sll", Float, nullable=False),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("accepted_at", DateTime, nullable=True),
    Column("picked_up_at", DateTime, nullable=True),
    Column("delivered_at", DateTime, nullable=True),
)
settings_t = Table("delivery_settings", _md,
    Column("key", String, primary_key=True),
    Column("value", Float, nullable=False),
)
sizes_t = Table("part_size_class", _md,
    Column("part_id", Integer, ForeignKey("part_listings.id", ondelete="CASCADE"), primary_key=True),
    Column("size_class", String, nullable=False),
)
_OWN_TABLES = [orders, items_t, events_t, jobs, settings_t, sizes_t]
PARTS = models.PartListing.__table__
USERS = models.User.__table__


async def ensure_delivery_tables():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: _md.create_all(c, tables=_OWN_TABLES, checkfirst=True))
    except Exception:
        log.exception("Could not create delivery tables")


def _iso(d):
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()}


async def _event(db, order_id, status, note=None, actor_id=None):
    await db.execute(insert(events_t).values(order_id=order_id, status=status, note=note, actor_id=actor_id))


async def _touch(db, order_id, status):
    await db.execute(update(orders).where(orders.c.id == order_id).values(status=status, updated_at=datetime.utcnow()))


async def _setting(db, key):
    row = (await db.execute(select(settings_t.c.value).where(settings_t.c.key == key))).first()
    return None if row is None else float(row[0])


def _default_size(part):
    t = (part.name or "").lower()
    return "bulky" if any(w in t for w in BULKY_WORDS) else "small"


async def _fee_for(db, vehicle_class):
    fee = await _setting(db, "van_fee_sll" if vehicle_class == "van" else "okada_fee_sll")
    pct = await _setting(db, "platform_pct")
    if fee is None or pct is None:
        raise HTTPException(503, "Delivery is not available yet")
    cut = round(fee * pct / 100.0, 2)
    return round(fee, 2), cut, round(fee - cut, 2)


async def _order_or_404(db, order_id, **where):
    q = select(orders).where(orders.c.id == order_id)
    for col, val in where.items():
        q = q.where(orders.c[col] == val)
    row = (await db.execute(q)).first()
    if not row:
        raise HTTPException(404, "Order not found")
    return row


async def _order_items(db, order_id):
    rows = (await db.execute(select(items_t).where(items_t.c.order_id == order_id).order_by(items_t.c.id))).all()
    return [_iso(dict(r._mapping)) for r in rows]


async def _order_events(db, order_id):
    rows = (await db.execute(select(events_t).where(events_t.c.order_id == order_id).order_by(events_t.c.id))).all()
    return [_iso(dict(r._mapping)) for r in rows]


async def _verified_driver(db, user, need_online=False):
    row = (await db.execute(select(drivers_t).where(drivers_t.c.user_id == user.id))).first()
    if not row or row.status != "verified":
        raise HTTPException(403, "Only verified drivers can do this")
    if need_online and not row.is_online:
        raise HTTPException(403, "Go online first")
    return row


async def _restore_stock(db, order_id):
    for it in (await db.execute(select(items_t).where(items_t.c.order_id == order_id))).all():
        if it.part_id is not None:
            await db.execute(update(PARTS).where(PARTS.c.id == it.part_id)
                             .values(stock_quantity=PARTS.c.stock_quantity + it.qty))


class ItemIn(BaseModel):
    part_id: int
    qty: int = Field(default=1, ge=1, le=20)


class OrderIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1, max_length=20)
    delivery_address: str = Field(min_length=5, max_length=200)
    delivery_area: str = Field(min_length=2, max_length=60)
    phone: str = Field(min_length=6, max_length=25)


class CodeIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class SettingsIn(BaseModel):
    okada_fee_sll: float | None = Field(default=None, ge=0, le=10_000_000)
    van_fee_sll: float | None = Field(default=None, ge=0, le=10_000_000)
    platform_pct: float | None = Field(default=None, ge=0, le=100)


@router.post("/orders", status_code=201)
async def place_order(body: OrderIn, user: models.User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    ids = [i.part_id for i in body.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "List each part once and use the quantity field")
    rows = (await db.execute(select(PARTS).where(PARTS.c.id.in_(ids)))).all()
    by_id = {r.id: r for r in rows}
    if len(by_id) != len(ids):
        raise HTTPException(404, "One of these parts is no longer listed")
    owners = {(r.vendor_id or r.seller_id) for r in rows}
    if len(owners) != 1:
        raise HTTPException(422, "Order from one seller at a time")
    seller_id = owners.pop()
    if seller_id == user.id:
        raise HTTPException(422, "You cannot order your own part")
    overrides = {r.part_id: r.size_class for r in
                 (await db.execute(select(sizes_t).where(sizes_t.c.part_id.in_(ids)))).all()}
    lines, total, vehicle = [], 0.0, "bike"
    for it in body.items:
        part = by_id[it.part_id]
        size = overrides.get(part.id) or _default_size(part)
        if size == "bulky":
            vehicle = "van"
        total += float(part.price_sll) * it.qty
        lines.append((part, it.qty, size))
    fee, cut, payout = await _fee_for(db, vehicle)
    for _ in range(5):
        code = "SAZ-" + "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(6))
        if not (await db.execute(select(orders.c.id).where(orders.c.code == code))).first():
            break
    delivery_code = f"{secrets.randbelow(10**6):06d}"
    try:
        res = await db.execute(insert(orders).values(
            code=code, buyer_id=user.id, seller_id=seller_id, status="awaiting_payment",
            delivery_address=body.delivery_address.strip(), delivery_area=body.delivery_area.strip(),
            pickup_area=(lines[0][0].location or None), buyer_phone=body.phone.strip(),
            vehicle_class=vehicle, parts_total_sll=round(total, 2), delivery_fee_sll=fee,
            platform_fee_sll=cut, driver_payout_sll=payout, delivery_code=delivery_code))
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Please try again")
    order_id = res.inserted_primary_key[0]
    for part, qty, size in lines:
        got = await db.execute(update(PARTS).where(PARTS.c.id == part.id, PARTS.c.stock_quantity >= qty)
                               .values(stock_quantity=PARTS.c.stock_quantity - qty))
        if got.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, f"Not enough stock for {part.name}")
        await db.execute(insert(items_t).values(order_id=order_id, part_id=part.id, title=part.name,
                                                unit_price_sll=float(part.price_sll), qty=qty, size_class=size))
    await _event(db, order_id, "awaiting_payment", "Order placed", user.id)
    await db.commit()
    return await _buyer_view(db, order_id)


async def _buyer_view(db, order_id):
    o = (await db.execute(select(orders).where(orders.c.id == order_id))).first()
    d = _iso(dict(o._mapping))
    d["items"], d["events"] = await _order_items(db, order_id), await _order_events(db, order_id)
    d["total_to_pay_sll"] = round(d["parts_total_sll"] + d["delivery_fee_sll"], 2)
    for hidden in ("platform_fee_sll", "driver_payout_sll", "code_attempts"):
        d.pop(hidden, None)
    return d


@router.get("/orders")
async def my_orders(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(orders).where(orders.c.buyer_id == user.id)
                             .order_by(orders.c.id.desc()).limit(100))).all()
    return {"count": len(rows), "orders": [
        {k: v for k, v in _iso(dict(r._mapping)).items() if k in
         ("id","code","status","parts_total_sll","delivery_fee_sll","created_at","delivery_area")} for r in rows]}


@router.get("/orders/{order_id}")
async def my_order(order_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _order_or_404(db, order_id, buyer_id=user.id)
    return await _buyer_view(db, order_id)


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _order_or_404(db, order_id, buyer_id=user.id)
    got = await db.execute(update(orders).where(
        orders.c.id == order_id, orders.c.status.in_(["awaiting_payment","confirmed"]))
        .values(status="cancelled", updated_at=datetime.utcnow()))
    if got.rowcount != 1:
        raise HTTPException(409, "This order can no longer be cancelled here.")
    await _restore_stock(db, order_id)
    await _event(db, order_id, "cancelled", "Cancelled by buyer", user.id)
    await db.commit()
    return {"id": order_id, "status": "cancelled"}


@router.get("/track/{code}")
async def track(code: str, db: AsyncSession = Depends(get_db)):
    o = (await db.execute(select(orders).where(orders.c.code == code.upper()))).first()
    if not o:
        raise HTTPException(404, "No order with that code")
    events = [{"status": e["status"], "at": e["created_at"]} for e in await _order_events(db, o.id)]
    return {"code": o.code, "status": o.status, "delivery_area": o.delivery_area,
            "vehicle_class": o.vehicle_class, "events": events}


@router.get("/seller/orders")
async def seller_orders(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(orders).where(orders.c.seller_id == user.id)
                             .order_by(orders.c.id.desc()).limit(100))).all()
    out = []
    for r in rows:
        d = _iso(dict(r._mapping))
        for hidden in ("delivery_code","code_attempts","delivery_address","buyer_phone","buyer_id",
                       "platform_fee_sll","driver_payout_sll","delivery_fee_sll"):
            d.pop(hidden, None)
        d["items"] = await _order_items(db, r.id)
        out.append(d)
    return {"count": len(out), "orders": out}


@router.post("/seller/orders/{order_id}/ready")
async def mark_ready(order_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    o = await _order_or_404(db, order_id, seller_id=user.id)
    got = await db.execute(update(orders).where(orders.c.id == order_id, orders.c.status == "confirmed")
                           .values(status="ready_for_pickup", updated_at=datetime.utcnow()))
    if got.rowcount != 1:
        raise HTTPException(409, "The order must be paid and confirmed before it can be marked ready")
    await db.execute(insert(jobs).values(order_id=order_id, status="open", vehicle_class=o.vehicle_class,
                                         fee_sll=o.delivery_fee_sll, payout_sll=o.driver_payout_sll))
    await _event(db, order_id, "ready_for_pickup", "Seller marked the parts ready", user.id)
    await db.commit()
    return {"id": order_id, "status": "ready_for_pickup"}


@router.get("/driver/jobs")
async def driver_jobs(scope: str = Query("open"), user: models.User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    driver = await _verified_driver(db, user, need_online=(scope == "open"))
    if scope == "open":
        rows = (await db.execute(
            select(jobs, orders.c.code, orders.c.pickup_area, orders.c.delivery_area)
            .select_from(jobs.join(orders, orders.c.id == jobs.c.order_id))
            .where(jobs.c.status == "open", jobs.c.vehicle_class == driver.vehicle_type)
            .order_by(jobs.c.id).limit(50))).all()
        return {"count": len(rows), "jobs": [
            {"id": r.id, "code": r.code, "vehicle_class": r.vehicle_class, "payout_sll": r.payout_sll,
             "pickup_area": r.pickup_area, "delivery_area": r.delivery_area} for r in rows]}
    if scope == "mine":
        rows = (await db.execute(
            select(jobs, orders).select_from(jobs.join(orders, orders.c.id == jobs.c.order_id))
            .where(jobs.c.driver_id == driver.id, jobs.c.status.in_(["accepted","picked_up"])))).all()
        out = []
        for r in rows:
            m = r._mapping
            seller = (await db.execute(select(USERS.c.full_name, USERS.c.phone)
                                       .where(USERS.c.id == m["seller_id"]))).first()
            out.append(_iso({
                "id": m["id"], "status": m["status"], "code": m["code"], "payout_sll": m["payout_sll"],
                "pickup_area": m["pickup_area"], "seller_name": seller.full_name if seller else None,
                "seller_phone": seller.phone if seller else None,
                "delivery_address": m["delivery_address"], "delivery_area": m["delivery_area"],
                "buyer_phone": m["buyer_phone"], "items": await _order_items(db, m["order_id"])}))
        return {"count": len(out), "jobs": out}
    raise HTTPException(422, "scope must be open or mine")


@router.post("/driver/jobs/{job_id}/accept")
async def accept_job(job_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    driver = await _verified_driver(db, user, need_online=True)
    busy = (await db.execute(select(jobs.c.id).where(
        jobs.c.driver_id == driver.id, jobs.c.status.in_(["accepted","picked_up"])))).first()
    if busy:
        raise HTTPException(409, "Finish your current delivery first")
    got = await db.execute(update(jobs).where(
        jobs.c.id == job_id, jobs.c.status == "open", jobs.c.vehicle_class == driver.vehicle_type)
        .values(status="accepted", driver_id=driver.id, accepted_at=datetime.utcnow()))
    if got.rowcount != 1:
        raise HTTPException(409, "This job is no longer available")
    job = (await db.execute(select(jobs).where(jobs.c.id == job_id))).first()
    await _touch(db, job.order_id, "driver_assigned")
    await _event(db, job.order_id, "driver_assigned", "A driver accepted the delivery", user.id)
    await db.commit()
    return {"id": job_id, "status": "accepted"}


async def _my_job_step(db, user, job_id, from_status, to_status, order_status, note, stamp):
    driver = await _verified_driver(db, user)
    got = await db.execute(update(jobs).where(
        jobs.c.id == job_id, jobs.c.driver_id == driver.id, jobs.c.status == from_status)
        .values(status=to_status, **{stamp: datetime.utcnow()}))
    if got.rowcount != 1:
        raise HTTPException(409, "This job is not in the right state for that step")
    job = (await db.execute(select(jobs).where(jobs.c.id == job_id))).first()
    await _touch(db, job.order_id, order_status)
    await _event(db, job.order_id, order_status, note, user.id)
    return job


@router.post("/driver/jobs/{job_id}/pickup")
async def pickup_job(job_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _my_job_step(db, user, job_id, "accepted", "picked_up", "out_for_delivery",
                       "Driver collected the parts", "picked_up_at")
    await db.commit()
    return {"id": job_id, "status": "picked_up"}


@router.post("/driver/jobs/{job_id}/deliver")
async def deliver_job(job_id: int, body: CodeIn, user: models.User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    driver = await _verified_driver(db, user)
    job = (await db.execute(select(jobs).where(jobs.c.id == job_id, jobs.c.driver_id == driver.id))).first()
    if not job or job.status != "picked_up":
        raise HTTPException(409, "This job is not out for delivery")
    order = (await db.execute(select(orders).where(orders.c.id == job.order_id))).first()
    if order.code_attempts >= MAX_CODE_ATTEMPTS:
        raise HTTPException(423, "Too many wrong codes.")
    if not secrets.compare_digest(body.code, order.delivery_code):
        await db.execute(update(orders).where(orders.c.id == order.id)
                         .values(code_attempts=orders.c.code_attempts + 1))
        await db.commit()
        left = MAX_CODE_ATTEMPTS - order.code_attempts - 1
        raise HTTPException(400, f"Wrong code. {max(left, 0)} tries left.")
    await _my_job_step(db, user, job_id, "picked_up", "delivered", "delivered",
                       "Delivered. Buyer's code entered", "delivered_at")
    await db.commit()
    return {"id": job_id, "status": "delivered", "payout_sll": job.payout_sll}


@router.post("/driver/jobs/{job_id}/release")
async def release_job(job_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    driver = await _verified_driver(db, user)
    got = await db.execute(update(jobs).where(
        jobs.c.id == job_id, jobs.c.driver_id == driver.id, jobs.c.status == "accepted")
        .values(status="open", driver_id=None, accepted_at=None))
    if got.rowcount != 1:
        raise HTTPException(409, "You can only release a job you have not collected yet")
    job = (await db.execute(select(jobs).where(jobs.c.id == job_id))).first()
    await _touch(db, job.order_id, "ready_for_pickup")
    await _event(db, job.order_id, "ready_for_pickup", "The driver released the job", user.id)
    await db.commit()
    return {"id": job_id, "status": "open"}


@router.get("/driver/earnings")
async def driver_earnings(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    driver = await _verified_driver(db, user)
    n, total = (await db.execute(select(func.count(jobs.c.id), func.coalesce(func.sum(jobs.c.payout_sll), 0))
                                 .where(jobs.c.driver_id == driver.id, jobs.c.status == "delivered"))).one()
    return {"deliveries": n, "earned_sll": round(float(total), 2)}


@router.get("/admin/orders")
async def admin_orders(status: str = Query("awaiting_payment"), admin=Depends(admin_actor),
                       db: AsyncSession = Depends(get_db)):
    q = select(orders).order_by(orders.c.id.desc()).limit(200)
    if status != "all":
        q = q.where(orders.c.status == status)
    rows = (await db.execute(q)).all()
    return {"count": len(rows), "orders": [_iso(dict(r._mapping)) for r in rows]}


@router.post("/admin/orders/{order_id}/confirm-payment")
async def confirm_payment(order_id: int, admin=Depends(admin_actor),
                          db: AsyncSession = Depends(get_db)):
    await _order_or_404(db, order_id)
    got = await db.execute(update(orders).where(orders.c.id == order_id, orders.c.status == "awaiting_payment")
                           .values(status="confirmed", updated_at=datetime.utcnow()))
    if got.rowcount != 1:
        raise HTTPException(409, "This order is not waiting for payment")
    await _event(db, order_id, "confirmed", "Payment confirmed", admin.id)
    await db.commit()
    return {"id": order_id, "status": "confirmed"}


@router.post("/admin/orders/{order_id}/complete")
async def force_complete(order_id: int, note: str = Query(..., min_length=3, max_length=200),
                         admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    o = await _order_or_404(db, order_id)
    if o.status not in ("driver_assigned", "picked_up", "out_for_delivery"):
        raise HTTPException(409, "Only an order that is with a driver can be completed manually")
    await db.execute(update(jobs).where(jobs.c.order_id == order_id)
                     .values(status="delivered", delivered_at=datetime.utcnow()))
    await _touch(db, order_id, "delivered")
    await _event(db, order_id, "delivered", "Completed by admin: " + note, admin.id)
    await db.commit()
    return {"id": order_id, "status": "delivered"}


@router.get("/admin/settings")
async def get_settings(admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(settings_t))).all()
    cur = {r.key: r.value for r in rows}
    return {k: cur.get(k) for k in ("okada_fee_sll", "van_fee_sll", "platform_pct")}


@router.put("/admin/settings")
async def put_settings(body: SettingsIn, admin=Depends(admin_actor),
                       db: AsyncSession = Depends(get_db)):
    for key, value in body.model_dump(exclude_none=True).items():
        if (await db.execute(select(settings_t).where(settings_t.c.key == key))).first():
            await db.execute(update(settings_t).where(settings_t.c.key == key).values(value=value))
        else:
            await db.execute(insert(settings_t).values(key=key, value=value))
    await db.commit()
    return await get_settings(admin, db)
