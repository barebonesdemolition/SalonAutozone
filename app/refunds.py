"""Refunds: money SalonAutoZone sends back to a buyer.

How a refund starts:
* the buyer cancels an order they've already paid for -> a refund to send straight away;
* the buyer reports a problem with a delivered order (wrong or broken part) -> an admin decides;
* an admin cancels a paid order, or refunds a delivered one.

An admin then sends the money by mobile money and marks it sent. A cancelled order never
reaches the shop or rider payout lists (those only include delivered orders). If a refund
is approved on a delivered order, the shop payout list shows it so the shop is paid less,
or not at all.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_user
from app.db import engine, get_db
from app.notify import notify, notify_admins
from app.routers.admin import admin_actor

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/refunds", tags=["Refunds"])

_md = MetaData()
Table("shop_orders", _md, Column("id", Integer, primary_key=True))
refunds = Table("refunds", _md,
    Column("id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("shop_orders.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("amount_sll", Float, nullable=False),
    Column("reason", String, nullable=False),
    # requested (buyer asked, admin to decide) | to_send | sent | declined
    Column("status", String, nullable=False, index=True),
    Column("source", String, nullable=False),        # buyer_cancel | buyer_problem | admin
    Column("refund_phone", String, nullable=True),
    Column("created_by", Integer, nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("decided_at", DateTime, nullable=True),
    Column("sent_at", DateTime, nullable=True),
    Column("sent_ref", String, nullable=True),
    Column("note", String, nullable=True),
)

PROBLEM_DAYS = 7
OPEN = ("requested", "to_send")
APPROVED = ("to_send", "sent")


async def ensure_refund_tables():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: _md.create_all(c, tables=[refunds], checkfirst=True))
    except Exception:
        log.exception("Could not create refund tables")


def _public(r):
    return {"id": r.id, "order_id": r.order_id, "amount_sll": r.amount_sll, "reason": r.reason, "status": r.status,
            "source": r.source, "refund_phone": r.refund_phone, "note": r.note, "sent_ref": r.sent_ref,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None}


def _total(o):
    return round((o.parts_total_sll or 0) + (o.delivery_fee_sll or 0), 2)


async def _refund_phone(db, o):
    """The number the buyer paid from, if they told us, else their order phone."""
    from app.payments import order_payments
    try:
        p = (await db.execute(select(order_payments.c.payer_phone).where(
            order_payments.c.order_id == o.id, order_payments.c.status == "approved",
            order_payments.c.payer_phone.isnot(None)).order_by(order_payments.c.id.desc()))).first()
    except Exception:
        p = None
    return (p.payer_phone if p else None) or o.buyer_phone


async def refunded_sll(db, order_ids):
    """Approved refund totals per order (for the shop payout list)."""
    if not order_ids:
        return {}
    try:
        async with db.begin_nested():
            rows = (await db.execute(select(refunds.c.order_id, func.sum(refunds.c.amount_sll)).where(
                refunds.c.order_id.in_(order_ids), refunds.c.status.in_(APPROVED)).group_by(refunds.c.order_id))).all()
    except Exception:
        return {}
    return {r[0]: round(float(r[1]), 2) for r in rows}


async def create_refund(db, o, amount, reason, source, status, actor_id=None):
    """Add a refund row (caller commits)."""
    res = await db.execute(insert(refunds).values(
        order_id=o.id, amount_sll=round(amount, 2), reason=reason[:300], status=status, source=source,
        refund_phone=await _refund_phone(db, o), created_by=actor_id,
        decided_at=datetime.utcnow() if status == "to_send" else None))
    return res.inserted_primary_key[0]


# ---------- buyer ----------

async def _buyer_order(db, order_id, user):
    from app.shop import orders
    o = (await db.execute(select(orders).where(orders.c.id == order_id, orders.c.buyer_id == user.id))).first()
    if not o:
        raise HTTPException(404, "Order not found")
    return o


@router.get("/orders/{order_id}")
async def order_refunds(order_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.shop import events_t
    o = await _buyer_order(db, order_id, user)
    rows = (await db.execute(select(refunds).where(refunds.c.order_id == order_id).order_by(refunds.c.id.desc()))).all()
    delivered_at = (await db.execute(select(events_t.c.created_at).where(
        events_t.c.order_id == order_id, events_t.c.status == "delivered").order_by(events_t.c.id.desc()))).scalar()
    can_report = (o.status == "delivered" and delivered_at is not None
                  and delivered_at > datetime.utcnow() - timedelta(days=PROBLEM_DAYS)
                  and not any(r.status in OPEN for r in rows))
    return {"refunds": [_public(r) for r in rows], "can_report_problem": can_report, "problem_days": PROBLEM_DAYS}


class ProblemIn(BaseModel):
    reason: str = Field(min_length=10, max_length=300)


@router.post("/orders/{order_id}/problem", status_code=201)
async def report_problem(order_id: int, body: ProblemIn, user: models.User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """Wrong, broken or missing part: ask for money back. An admin looks at it."""
    info = await order_refunds(order_id, user, db)
    if not info["can_report_problem"]:
        raise HTTPException(409, f"Problems can be reported within {PROBLEM_DAYS} days of delivery, once per order.")
    o = await _buyer_order(db, order_id, user)
    rid = await create_refund(db, o, _total(o), body.reason.strip(), "buyer_problem", "requested", user.id)
    await db.commit()
    await notify_admins(f"Problem reported: {o.code}", body.reason.strip()[:140], "/admin", "refund")
    await notify(o.seller_id, f"Buyer reported a problem with {o.code}", body.reason.strip()[:140], "/my-account?tab=sales", "refund")
    return {"id": rid, "status": "requested"}


# ---------- admin ----------

@router.get("/admin")
async def admin_refunds(status: str = "open", admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    from app.shop import orders
    q = (select(refunds, orders.c.code, orders.c.status.label("order_status"), orders.c.shop_paid_at,
                orders.c.parts_total_sll, orders.c.delivery_fee_sll)
         .select_from(refunds.join(orders, orders.c.id == refunds.c.order_id)).order_by(refunds.c.id.desc()).limit(200))
    if status == "open":
        q = q.where(refunds.c.status.in_(OPEN))
    elif status != "all":
        q = q.where(refunds.c.status == status)
    out = []
    for r in (await db.execute(q)).all():
        d = _public(r)
        d.update(order_code=r.code, order_status=r.order_status, order_total_sll=round(r.parts_total_sll + r.delivery_fee_sll, 2),
                 shop_already_paid=r.shop_paid_at is not None)
        out.append(d)
    return {"count": len(out), "refunds": out}


async def _refund_or_404(db, rid):
    r = (await db.execute(select(refunds).where(refunds.c.id == rid))).first()
    if not r:
        raise HTTPException(404, "Refund not found")
    return r


async def _order(db, order_id):
    from app.shop import orders
    return (await db.execute(select(orders).where(orders.c.id == order_id))).first()


class ApproveIn(BaseModel):
    amount_sll: float | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=200)


@router.post("/admin/{rid}/approve")
async def approve(rid: int, body: ApproveIn, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    r = await _refund_or_404(db, rid)
    if r.status != "requested":
        raise HTTPException(409, "This refund has already been decided")
    o = await _order(db, r.order_id)
    amount = body.amount_sll or r.amount_sll
    if amount > _total(o):
        raise HTTPException(400, "A refund can't be more than the buyer paid")
    await db.execute(update(refunds).where(refunds.c.id == rid).values(
        status="to_send", amount_sll=round(amount, 2), decided_at=datetime.utcnow(), note=(body.note or "").strip() or None))
    await db.commit()
    await notify(o.buyer_id, f"Refund approved for {o.code}", f"We'll send you Le {amount:,.0f} soon.", f"/orders?id={o.id}", "refund")
    return {"id": rid, "status": "to_send"}


class DeclineIn(BaseModel):
    note: str = Field(min_length=3, max_length=200)


@router.post("/admin/{rid}/decline")
async def decline(rid: int, body: DeclineIn, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    r = await _refund_or_404(db, rid)
    if r.status != "requested":
        raise HTTPException(409, "This refund has already been decided")
    await db.execute(update(refunds).where(refunds.c.id == rid).values(
        status="declined", decided_at=datetime.utcnow(), note=body.note.strip()))
    await db.commit()
    o = await _order(db, r.order_id)
    await notify(o.buyer_id, f"About your refund for {o.code}", body.note.strip(), f"/orders?id={o.id}", "refund")
    return {"id": rid, "status": "declined"}


class SentIn(BaseModel):
    reference: str | None = Field(default=None, max_length=60)


@router.post("/admin/{rid}/sent")
async def sent(rid: int, body: SentIn, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    got = await db.execute(update(refunds).where(refunds.c.id == rid, refunds.c.status == "to_send").values(
        status="sent", sent_at=datetime.utcnow(), sent_ref=(body.reference or "").strip() or None))
    if got.rowcount != 1:
        raise HTTPException(409, "This refund isn't waiting to be sent")
    await db.commit()
    r = await _refund_or_404(db, rid)
    o = await _order(db, r.order_id)
    await notify(o.buyer_id, f"Refund sent for {o.code}", f"We sent Le {r.amount_sll:,.0f} to {r.refund_phone}.", f"/orders?id={o.id}", "refund")
    return {"id": rid, "status": "sent"}


class AdminRefundIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    amount_sll: float | None = Field(default=None, gt=0)


CANCELLABLE_PAID = ("confirmed", "ready_for_pickup", "driver_assigned")


@router.post("/admin/orders/{order_id}")
async def admin_refund_order(order_id: int, body: AdminRefundIn, admin=Depends(admin_actor),
                             db: AsyncSession = Depends(get_db)):
    """Refund a paid order. Before pickup this also cancels it; after delivery the order stays delivered."""
    from app.shop import _event, _restore_stock, jobs, orders
    o = await _order(db, order_id)
    if not o:
        raise HTTPException(404, "Order not found")
    if o.status not in CANCELLABLE_PAID + ("delivered",):
        raise HTTPException(409, "Only a paid order that isn't on its way can be refunded. Wait until it's delivered.")
    open_ = (await db.execute(select(refunds.c.id).where(refunds.c.order_id == order_id, refunds.c.status.in_(OPEN)))).first()
    if open_:
        raise HTTPException(409, "This order already has a refund in progress")
    amount = body.amount_sll or _total(o)
    if amount > _total(o):
        raise HTTPException(400, "A refund can't be more than the buyer paid")
    rider = None
    if o.status in CANCELLABLE_PAID:
        got = await db.execute(update(orders).where(orders.c.id == order_id, orders.c.status == o.status)
                               .values(status="cancelled", updated_at=datetime.utcnow()))
        if got.rowcount != 1:
            raise HTTPException(409, "The order changed. Refresh and try again.")
        job = (await db.execute(select(jobs).where(jobs.c.order_id == order_id))).first()
        if job and job.status in ("open", "accepted"):
            await db.execute(update(jobs).where(jobs.c.id == job.id).values(status="cancelled"))
            if job.driver_id:
                from app.drivers import drivers as drivers_t
                rider = (await db.execute(select(drivers_t.c.user_id).where(drivers_t.c.id == job.driver_id))).scalar()
        await _restore_stock(db, order_id)
        await _event(db, order_id, "cancelled", "Cancelled and refunded by admin: " + body.reason.strip(), admin.id)
    rid = await create_refund(db, o, amount, body.reason.strip(), "admin", "to_send", admin.id)
    await db.commit()
    await notify(o.buyer_id, f"Refund on its way for {o.code}", f"Le {amount:,.0f}. {body.reason.strip()}", f"/orders?id={o.id}", "refund")
    if o.status in CANCELLABLE_PAID:
        await notify(o.seller_id, f"Order {o.code} was cancelled", "Don't hand the parts to a rider. The buyer is being refunded.", "/my-account?tab=sales", "cancel")
        if rider:
            await notify(rider, f"Job {o.code} was cancelled", "You don't need to collect it.", "/drive", "cancel")
    return {"id": rid, "status": "to_send", "order_status": "cancelled" if o.status in CANCELLABLE_PAID else o.status}
