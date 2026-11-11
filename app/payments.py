"""Payment portal for parts orders.

Two ways to pay:

* Mobile money by hand (works today): the buyer sends the total to the platform's
  Orange Money or Afrimoney number, then types the transaction ID from the SMS.
  An admin checks it against the phone and approves it (or says it doesn't match).
* Online through Monime (Orange Money, Afrimoney, cards), switched on only when
  MONIME_ACCESS_TOKEN and MONIME_SPACE_ID are set. The buyer is sent to Monime's
  checkout page. We never trust what a browser or webhook *says* about a payment:
  the order is only marked paid after we ask Monime's API ourselves that the
  checkout session is completed for the right amount.
"""
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_user
from app.config import get_settings
from app.db import engine, get_db
from app.routers.admin import admin_actor
from app.notify import notify, notify_admins
from app.shop import alert_order_paid, mark_order_paid, orders

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pay", tags=["Payments"])

_md = MetaData()
Table("shop_orders", _md, Column("id", Integer, primary_key=True))
order_payments = Table("order_payments", _md,
    Column("id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("shop_orders.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("user_id", Integer, nullable=True),
    # orange_money | afrimoney | monime
    Column("method", String, nullable=False),
    # submitted (waiting for an admin) | pending (online, not finished) | approved | rejected | cancelled
    Column("status", String, nullable=False, index=True),
    Column("amount_sll", Float, nullable=False),
    Column("reference", String, nullable=True),        # transaction ID the buyer typed
    Column("reference_key", String, nullable=True, index=True),  # same, upper-case letters and digits only
    Column("payer_phone", String, nullable=True),
    Column("provider_ref", String, nullable=True, index=True),   # Monime checkout session id
    Column("note", String, nullable=True),             # why it was rejected, or how it was settled
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("reviewed_at", DateTime, nullable=True),
    Column("reviewed_by", Integer, nullable=True),
    Column("checked_at", DateTime, nullable=True),
)

METHOD_LABELS = {"orange_money": "Orange Money", "afrimoney": "Afrimoney", "monime": "Online (Monime)"}
MAX_TRIES_PER_ORDER = 6
COMPLETED = {"completed", "paid", "succeeded", "success"}
CHECK_GAP_SECONDS = 5  # don't ask Monime about the same checkout more often than this


async def ensure_payment_tables():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: _md.create_all(c, tables=[order_payments], checkfirst=True))
    except Exception:
        log.exception("Could not create payment tables")


# ---------- settings ----------

def _digits(v):
    return "".join(ch for ch in (v or "") if ch.isdigit())


def monime_ready():
    cfg = get_settings()
    return bool(cfg.MONIME_ACCESS_TOKEN and cfg.MONIME_SPACE_ID)


def methods():
    cfg = get_settings()
    out = []
    if cfg.ORANGE_MONEY_NUMBER:
        out.append({"id": "orange_money", "label": "Orange Money", "number": cfg.ORANGE_MONEY_NUMBER, "online": False})
    if cfg.AFRIMONEY_NUMBER:
        out.append({"id": "afrimoney", "label": "Afrimoney", "number": cfg.AFRIMONEY_NUMBER, "online": False})
    if monime_ready():
        out.insert(0, {"id": "monime", "label": "Pay online", "number": None, "online": True,
                       "detail": "Orange Money, Afrimoney or bank card. Confirmed straight away."})
    return out


async def _shop(db, seller_id):
    return (await db.execute(select(models.Business).where(models.Business.owner_id == seller_id))).scalars().first()


async def order_methods(db, o):
    """Where the buyer of this order sends money: the shop's own numbers, or SalonAutoZone's."""
    if getattr(o, "pay_to", "platform") != "shop":
        return methods(), "SalonAutoZone"
    biz = await _shop(db, o.seller_id)
    out = []
    if biz and biz.pay_orange_money:
        out.append({"id": "orange_money", "label": "Orange Money", "number": biz.pay_orange_money, "online": False})
    if biz and biz.pay_afrimoney:
        out.append({"id": "afrimoney", "label": "Afrimoney", "number": biz.pay_afrimoney, "online": False})
    return out, (biz.name if biz else "the shop")


def reference_key(ref):
    return re.sub(r"[^A-Z0-9]", "", (ref or "").upper())


def to_monime_minor(amount_sll):
    """Site amount (new leones) -> Monime's amount: new leones (SLE) in cents."""
    per_sle = get_settings().MONIME_SLL_PER_SLE or 1
    return int(round(amount_sll / per_sle * 100))


def _total(o):
    return round((o.parts_total_sll or 0) + (o.delivery_fee_sll or 0), 2)


def _public_payment(p):
    return {
        "id": p.id, "method": p.method, "method_label": METHOD_LABELS.get(p.method, p.method),
        "status": p.status, "amount_sll": p.amount_sll, "reference": p.reference,
        "payer_phone": p.payer_phone, "note": p.note,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
    }


# ---------- helpers other modules use ----------

async def latest_payments(db, order_ids):
    """The newest payment attempt for each order, for the admin's order list."""
    if not order_ids:
        return {}
    try:
        async with db.begin_nested():
            rows = (await db.execute(select(order_payments).where(order_payments.c.order_id.in_(order_ids))
                                     .order_by(order_payments.c.id))).all()
    except Exception:  # table not created yet on this database
        return {}
    out = {}
    for r in rows:
        out[r.order_id] = _public_payment(r)
    return out


async def settle_open_payments(db, order_id, actor_id, note="Payment confirmed by admin"):
    """When an order is confirmed some other way, close the attempts still open for it."""
    try:
        async with db.begin_nested():
            await db.execute(update(order_payments).where(
                order_payments.c.order_id == order_id, order_payments.c.status == "submitted")
                .values(status="approved", reviewed_at=datetime.utcnow(), reviewed_by=actor_id, note=note))
            await db.execute(update(order_payments).where(
                order_payments.c.order_id == order_id, order_payments.c.status == "pending")
                .values(status="cancelled", note="Paid another way"))
    except Exception:
        log.exception("Could not settle payments for order %s", order_id)


async def paid_with(db, order_id):
    """How an order was paid, for the receipt."""
    try:
        async with db.begin_nested():
            p = (await db.execute(select(order_payments).where(
                order_payments.c.order_id == order_id, order_payments.c.status == "approved")
                .order_by(order_payments.c.id.desc()))).first()
    except Exception:
        return None
    if not p:
        return None
    return {"method": METHOD_LABELS.get(p.method, p.method), "reference": p.reference or p.provider_ref}


# ---------- buyer ----------

async def _buyer_order(db, order_id, user):
    o = (await db.execute(select(orders).where(orders.c.id == order_id, orders.c.buyer_id == user.id))).first()
    if not o:
        raise HTTPException(404, "Order not found")
    return o


async def _attempts(db, order_id):
    return (await db.execute(select(order_payments).where(order_payments.c.order_id == order_id)
                             .order_by(order_payments.c.id.desc()))).all()


@router.get("/orders/{order_id}")
async def payment_page(order_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Everything the pay page needs: amount, where to send it, and earlier attempts."""
    o = await _buyer_order(db, order_id, user)
    tries = await _attempts(db, order_id)
    cfg = get_settings()
    ways, payee = await order_methods(db, o)
    shop_phone = None
    if o.pay_to == "shop":
        biz = await _shop(db, o.seller_id)
        shop_phone = _digits(biz.whatsapp or biz.call_phone) if biz else None
    return {
        "pay_to": o.pay_to, "payee": payee, "payee_whatsapp": shop_phone or None,
        "order_id": o.id, "code": o.code, "status": o.status, "amount_sll": _total(o),
        "parts_total_sll": o.parts_total_sll, "delivery_fee_sll": o.delivery_fee_sll,
        "buyer_phone": o.buyer_phone, "methods": ways,
        "waiting_for_check": any(t.status == "submitted" for t in tries),
        "online_pending": any(t.status == "pending" for t in tries),
        "tries_left": max(0, MAX_TRIES_PER_ORDER - len([t for t in tries if t.method != "monime"])),
        "payments": [_public_payment(t) for t in tries],
        "whatsapp": _digits(cfg.ADMIN_WHATSAPP) or None,
    }


class ManualPaymentIn(BaseModel):
    method: str = Field(pattern="^(orange_money|afrimoney)$")
    reference: str = Field(min_length=4, max_length=40)
    payer_phone: str = Field(min_length=6, max_length=24)


@router.post("/orders/{order_id}/manual", status_code=201)
async def submit_manual(order_id: int, body: ManualPaymentIn, user: models.User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """The buyer has sent mobile money and gives us the transaction ID to check."""
    o = await _buyer_order(db, order_id, user)
    if o.status != "awaiting_payment":
        raise HTTPException(409, "This order doesn't need paying any more")
    if body.method not in {m["id"] for m in (await order_methods(db, o))[0]}:
        raise HTTPException(400, "That payment method isn't available")
    key = reference_key(body.reference)
    if len(key) < 4:
        raise HTTPException(400, "Type the transaction ID exactly as it is in your SMS")
    if len(_digits(body.payer_phone)) < 6:
        raise HTTPException(400, "Enter the phone number you sent the money from")
    tries = await _attempts(db, order_id)
    if any(t.status == "submitted" for t in tries):
        raise HTTPException(409, "We're already checking a payment for this order. We'll update it soon.")
    if len([t for t in tries if t.method != "monime"]) >= MAX_TRIES_PER_ORDER:
        raise HTTPException(429, "Too many tries for this order. Please message us on WhatsApp.")
    used = (await db.execute(select(order_payments.c.id).where(
        order_payments.c.reference_key == key, order_payments.c.status.in_(["submitted", "approved"])))).first()
    if used:
        raise HTTPException(409, "That transaction ID has already been used. Check the SMS and try again.")
    res = await db.execute(insert(order_payments).values(
        order_id=o.id, user_id=user.id, method=body.method, status="submitted", amount_sll=_total(o),
        reference=body.reference.strip(), reference_key=key, payer_phone=body.payer_phone.strip()))
    pid = res.inserted_primary_key[0]
    await db.commit()
    label = METHOD_LABELS.get(body.method, body.method)
    await notify_admins(f"Payment to check: {o.code}", f"{label} ID {body.reference.strip()}, Le {_total(o):,.0f}, from {body.payer_phone.strip()}.", "/admin", "payment")
    return {"id": pid, "status": "submitted"}


# ---------- Monime (online) ----------

def _monime_headers(idempotency_key=None):
    cfg = get_settings()
    h = {"Authorization": "Bearer " + cfg.MONIME_ACCESS_TOKEN, "Monime-Space-Id": cfg.MONIME_SPACE_ID,
         "Content-Type": "application/json"}
    if cfg.MONIME_VERSION:
        h["Monime-Version"] = cfg.MONIME_VERSION
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    return h


async def monime_create_session(payload, idempotency_key):
    cfg = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(cfg.MONIME_API_BASE.rstrip("/") + "/v1/checkout-sessions",
                              json=payload, headers=_monime_headers(idempotency_key))
    if r.status_code >= 400:
        log.warning("Monime create session failed: %s %s", r.status_code, r.text[:300])
        raise HTTPException(502, "Online payment isn't working right now. Please pay with mobile money below.")
    return (r.json() or {}).get("result") or {}


async def monime_get_session(session_id):
    cfg = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(cfg.MONIME_API_BASE.rstrip("/") + "/v1/checkout-sessions/" + session_id,
                             headers=_monime_headers())
    if r.status_code >= 400:
        log.warning("Monime get session %s failed: %s", session_id, r.status_code)
        return None
    return (r.json() or {}).get("result") or {}


def _session_amount(session):
    total = 0
    for li in session.get("lineItems") or []:
        if isinstance(li, dict):
            price = li.get("price") or {}
            try:
                total += int(price.get("value") or 0) * int(li.get("quantity") or 1)
            except (TypeError, ValueError):
                return None
    return total or None


@router.post("/orders/{order_id}/online")
async def start_online(order_id: int, request: Request, user: models.User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Open a Monime checkout for this order and return the page to send the buyer to."""
    if not monime_ready():
        raise HTTPException(404, "Online payment isn't switched on yet")
    o = await _buyer_order(db, order_id, user)
    if o.pay_to == "shop":
        raise HTTPException(409, "This shop takes payment directly. Pay it with mobile money.")
    if o.status != "awaiting_payment":
        raise HTTPException(409, "This order doesn't need paying any more")
    recent = (await db.execute(select(func.count(order_payments.c.id)).where(
        order_payments.c.order_id == o.id, order_payments.c.method == "monime",
        order_payments.c.created_at > datetime.utcnow() - timedelta(hours=1)))).scalar() or 0
    if recent >= 5:
        raise HTTPException(429, "Too many tries. Wait a little, or pay with mobile money.")
    from app.main import _public_base
    base = _public_base(request)
    amount = _total(o)
    payload = {
        "name": f"SalonAutoZone order {o.code}",
        "description": "Car parts and delivery",
        "reference": o.code,
        "lineItems": [{"name": f"Order {o.code}: parts and delivery",
                       "price": {"currency": "SLE", "value": to_monime_minor(amount)}, "quantity": 1}],
        "successUrl": f"{base}/pay/{o.id}?back=done",
        "cancelUrl": f"{base}/pay/{o.id}?back=cancel",
        "metadata": {"order_id": str(o.id), "order_code": o.code},
    }
    session = await monime_create_session(payload, f"saz-{o.code}-{secrets.token_hex(6)}")
    sid, url = session.get("id"), session.get("redirectUrl")
    if not sid or not url:
        raise HTTPException(502, "Online payment isn't working right now. Please pay with mobile money below.")
    await db.execute(insert(order_payments).values(
        order_id=o.id, user_id=user.id, method="monime", status="pending", amount_sll=amount, provider_ref=sid))
    await db.commit()
    return {"redirect_url": url}


async def _verify_monime(db, payment):
    """Ask Monime whether this checkout was paid. Marks the order paid if so. True when paid."""
    session = await monime_get_session(payment.provider_ref)
    await db.execute(update(order_payments).where(order_payments.c.id == payment.id)
                     .values(checked_at=datetime.utcnow()))
    if not session:
        return False
    status = str(session.get("status") or "").lower()
    if status in {"expired", "cancelled", "canceled"}:
        await db.execute(update(order_payments).where(order_payments.c.id == payment.id, order_payments.c.status == "pending")
                         .values(status="cancelled", note="Checkout " + status))
        return False
    if status not in COMPLETED:
        return False
    paid_minor = _session_amount(session)
    if paid_minor is not None and paid_minor < to_monime_minor(payment.amount_sll):
        log.warning("Monime session %s paid %s, expected %s", payment.provider_ref, paid_minor,
                    to_monime_minor(payment.amount_sll))
        await db.execute(update(order_payments).where(order_payments.c.id == payment.id)
                         .values(status="submitted", note="Amount paid online is less than the order total. Check it."))
        return False
    got = await db.execute(update(order_payments).where(order_payments.c.id == payment.id, order_payments.c.status == "pending")
                           .values(status="approved", reviewed_at=datetime.utcnow(), note="Paid online"))
    if got.rowcount == 1:
        if not await mark_order_paid(db, payment.order_id, "Paid online (Monime)"):
            # Paid for an order that was cancelled or already paid: leave it for an admin to refund.
            await db.execute(update(order_payments).where(order_payments.c.id == payment.id)
                             .values(status="submitted", note="Paid online, but the order wasn't waiting for payment. Refund or confirm by hand."))
            return False
        await settle_open_payments(db, payment.order_id, None, note="Settled by online payment")
    return True


@router.post("/orders/{order_id}/check")
async def check_online(order_id: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The buyer is back from Monime: look up whether the payment went through."""
    await _buyer_order(db, order_id, user)
    pending = (await db.execute(select(order_payments).where(
        order_payments.c.order_id == order_id, order_payments.c.status == "pending",
        order_payments.c.method == "monime"))).all()
    paid = False
    if monime_ready():
        for p in pending:
            if p.checked_at and p.checked_at > datetime.utcnow() - timedelta(seconds=CHECK_GAP_SECONDS):
                continue
            paid = await _verify_monime(db, p) or paid
        await db.commit()
        if paid:
            await alert_order_paid(db, order_id)
    o = (await db.execute(select(orders.c.status).where(orders.c.id == order_id))).first()
    return {"paid": paid or o.status != "awaiting_payment", "status": o.status}


def _session_ids(obj, found, depth=0):
    if depth > 6 or len(found) > 10:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _session_ids(v, found, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:20]:
            _session_ids(v, found, depth + 1)
    elif isinstance(obj, str) and re.fullmatch(r"sps-[A-Za-z0-9_-]{4,80}", obj):
        found.add(obj)


@router.post("/monime/webhook")
async def monime_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Monime tells us something happened. We don't trust the message itself: we take the
    checkout session ids from it and ask Monime's API about each one we are waiting on."""
    if not monime_ready():
        return {"ok": True}
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}
    ids = set()
    _session_ids(body, ids)
    if not ids:
        return {"ok": True}
    rows = (await db.execute(select(order_payments).where(
        order_payments.c.provider_ref.in_(list(ids)), order_payments.c.status == "pending"))).all()
    paid = [p.order_id for p in rows if await _verify_monime(db, p)]
    await db.commit()
    for oid in paid:
        await alert_order_paid(db, oid)
    return {"ok": True}


# ---------- admin ----------

@router.get("/admin/payments")
async def admin_payments(status: str = "submitted", admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    q = (select(order_payments, orders.c.code, orders.c.status.label("order_status"),
                orders.c.parts_total_sll, orders.c.delivery_fee_sll)
         .select_from(order_payments.join(orders, orders.c.id == order_payments.c.order_id))
         .order_by(order_payments.c.id.desc()).limit(200))
    if status != "all":
        q = q.where(order_payments.c.status == status)
    rows = (await db.execute(q)).all()
    out = []
    for r in rows:
        d = _public_payment(r)
        d.update(order_id=r.order_id, order_code=r.code, order_status=r.order_status,
                 order_total_sll=round((r.parts_total_sll or 0) + (r.delivery_fee_sll or 0), 2))
        out.append(d)
    return {"count": len(out), "payments": out}


async def _payment_or_404(db, pid):
    p = (await db.execute(select(order_payments).where(order_payments.c.id == pid))).first()
    if not p:
        raise HTTPException(404, "Payment not found")
    return p


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


async def _approve(db, p, actor_id, who):
    if p.status != "submitted":
        raise HTTPException(409, "This payment has already been dealt with")
    note = f"Payment confirmed by {who} ({METHOD_LABELS.get(p.method, p.method)} {p.reference or ''})".strip()
    if not await mark_order_paid(db, p.order_id, note, actor_id):
        raise HTTPException(409, "This order is not waiting for payment (it may be cancelled or already paid)")
    await db.execute(update(order_payments).where(order_payments.c.id == p.id)
                     .values(status="approved", reviewed_at=datetime.utcnow(), reviewed_by=actor_id, note=None))
    await settle_open_payments(db, p.order_id, actor_id)
    await db.commit()
    await alert_order_paid(db, p.order_id)
    return {"id": p.id, "status": "approved", "order_status": "confirmed"}


async def _reject(db, p, actor_id, reason):
    if p.status != "submitted":
        raise HTTPException(409, "This payment has already been dealt with")
    await db.execute(update(order_payments).where(order_payments.c.id == p.id)
                     .values(status="rejected", reviewed_at=datetime.utcnow(), reviewed_by=actor_id, note=reason.strip()))
    await db.commit()
    o = (await db.execute(select(orders.c.buyer_id, orders.c.code).where(orders.c.id == p.order_id))).first()
    await notify(o.buyer_id, f"We couldn't find your payment for {o.code}", reason.strip(), f"/pay/{p.order_id}", "payment")
    return {"id": p.id, "status": "rejected"}


@router.post("/admin/payments/{pid}/approve")
async def approve(pid: int, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    """The money is on the phone: confirm the order."""
    return await _approve(db, await _payment_or_404(db, pid), admin.id, "SalonAutoZone")


@router.post("/admin/payments/{pid}/reject")
async def reject(pid: int, body: RejectIn, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    """Nothing arrived, or the amount is wrong: tell the buyer why so they can try again."""
    return await _reject(db, await _payment_or_404(db, pid), admin.id, body.reason)


# ---------- shop (seller) checks payments sent to its own number ----------

async def _seller_payment(db, pid, user):
    p = await _payment_or_404(db, pid)
    o = (await db.execute(select(orders.c.seller_id, orders.c.pay_to).where(orders.c.id == p.order_id))).first()
    if not o or o.seller_id != user.id or o.pay_to != "shop":
        raise HTTPException(404, "Payment not found")
    return p


@router.post("/seller/payments/{pid}/approve")
async def seller_approve(pid: int, user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The shop has the money on its phone."""
    return await _approve(db, await _seller_payment(db, pid, user), user.id, "the shop")


@router.post("/seller/payments/{pid}/reject")
async def seller_reject(pid: int, body: RejectIn, user: models.User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    return await _reject(db, await _seller_payment(db, pid, user), user.id, body.reason)


@router.get("/admin/status")
async def admin_status(admin=Depends(admin_actor)):
    """Which ways to pay are switched on, for the admin dashboard."""
    cfg = get_settings()
    return {"methods": methods(), "monime_ready": monime_ready(),
            "monime_api": cfg.MONIME_API_BASE, "webhook_path": "/api/pay/monime/webhook"}
