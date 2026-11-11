"""Traffic and customer data, counted on our own site (no Google Analytics, no outside trackers).

What is recorded, per event: the page, the site that sent the visitor (just its name, e.g.
facebook.com), a random visitor id the browser keeps (not linked to a name), phone or
computer, and for searches the words typed and how many results came back. Shop and part
pages are linked to the seller so shops can see their own numbers.

Where buyers are comes from orders (delivery area and map pin), which buyers give us to get
their parts delivered. Pins are rounded to about 100 m before they are shown on any map.

Events older than ANALYTICS_KEEP_DAYS are deleted at start-up.
"""
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import (Boolean, Column, DateTime, Integer, MetaData, String, Table, and_, delete, distinct, func,
                        insert, select)
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import get_current_user
from app.db import AsyncSessionLocal, engine, get_db
from app.routers.admin import admin_actor

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

KEEP_DAYS = 400
_md = MetaData()
events = Table("analytics_events", _md,
    Column("id", Integer, primary_key=True),
    Column("at", DateTime, nullable=False, default=datetime.utcnow, index=True),
    Column("kind", String, nullable=False, index=True),     # view | contact | search
    Column("path", String, nullable=True),
    Column("ref", String, nullable=True),                   # referring site's host
    Column("visitor", String, nullable=True, index=True),
    Column("device", String, nullable=True),                # phone | computer
    Column("signed_in", Boolean, nullable=True),
    Column("seller_id", Integer, nullable=True, index=True),
    Column("part_id", Integer, nullable=True),
    Column("query", String, nullable=True),
    Column("results", Integer, nullable=True),
    Column("detail", String, nullable=True),                # contact: whatsapp | call
)

BOTS = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|telegram|headless|lighthouse|curl|python|wget", re.I)
_hits = defaultdict(list)


async def ensure_analytics_tables():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: _md.create_all(c, checkfirst=True))
            await conn.execute(delete(events).where(events.c.at < datetime.utcnow() - timedelta(days=KEEP_DAYS)))
    except Exception:
        log.exception("Could not create analytics tables")


def _too_many(key, limit=120, window=60):
    now = time.monotonic()
    hits = [t for t in _hits[key] if now - t < window]
    hits.append(now)
    _hits[key] = hits
    if len(_hits) > 5000:  # keep memory small
        _hits.clear()
    return len(hits) > limit


async def _seller_for_path(db, path):
    """Which seller a page belongs to: /store/<slug>, /seller/<id>, /part/<id>, /vehicle/<id>."""
    m = re.match(r"^/(store|seller|part|vehicle)/([^/?#]+)", path or "")
    if not m:
        return None, None
    kind, key = m.groups()
    try:
        if kind == "store":
            b = (await db.execute(select(models.Business.owner_id).where(models.Business.slug == key))).first()
            return (b[0] if b else None), None
        if not key.isdigit():
            return None, None
        if kind == "seller":
            return int(key), None
        if kind == "part":
            p = (await db.execute(select(models.PartListing.vendor_id, models.PartListing.seller_id)
                                  .where(models.PartListing.id == int(key)))).first()
            return ((p[0] or p[1]) if p else None), int(key)
        v = (await db.execute(select(models.VehicleListing.seller_id).where(models.VehicleListing.id == int(key)))).first()
        return (v[0] if v else None), None
    except Exception:
        return None, None


class HitIn(BaseModel):
    kind: str = Field(pattern="^(view|contact)$")
    path: str = Field(max_length=300)
    ref: str | None = Field(default=None, max_length=300)
    visitor: str | None = Field(default=None, max_length=40)
    detail: str | None = Field(default=None, max_length=20)
    signed_in: bool | None = None


@router.post("/hit", status_code=204)
async def hit(body: HitIn, request: Request):
    """Called by every page (site.js). Never fails the page."""
    ua = request.headers.get("user-agent", "")
    if not ua or BOTS.search(ua):
        return
    ip = request.client.host if request.client else "?"
    if _too_many(ip):
        return
    ref = None
    if body.ref:
        host = (urlparse(body.ref).hostname or "").lower()
        own = (request.url.hostname or "").lower()
        if host and host != own and not host.endswith(".onrender.com") and not host.endswith("salonautozone.com"):
            ref = host[:80]
    path = body.path.split("?")[0].split("#")[0][:200]
    try:
        async with AsyncSessionLocal() as db:
            seller_id, part_id = await _seller_for_path(db, path)
            await db.execute(insert(events).values(
                kind=body.kind, path=path, ref=ref, visitor=re.sub(r"[^A-Za-z0-9-]", "", body.visitor or "")[:40] or None,
                device="phone" if re.search(r"Mobi|Android|iPhone", ua) else "computer", signed_in=body.signed_in,
                seller_id=seller_id, part_id=part_id,
                detail=body.detail if body.detail in ("whatsapp", "call") else None))
            await db.commit()
    except Exception:
        log.exception("Could not record a page hit")


async def record_search(q, results, request=None):
    """Called by the parts search. Records what people look for, and what they can't find."""
    q = (q or "").strip()
    if len(q) < 2:
        return
    if request is not None:
        ua = request.headers.get("user-agent", "")
        if not ua or BOTS.search(ua):
            return
        if request.headers.get("x-saz-page", "") != "1":  # page loads only; not our own internal calls
            return
        visitor = re.sub(r"[^A-Za-z0-9-]", "", request.headers.get("x-saz-visitor", ""))[:40] or None
    else:
        visitor = None
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(insert(events).values(kind="search", query=q.lower()[:100], results=int(results or 0),
                                                   visitor=visitor, path="/search"))
            await db.commit()
    except Exception:
        log.exception("Could not record a search")


# ---------- reports ----------

def _day(dt):
    return dt.strftime("%Y-%m-%d")


@router.get("/admin/summary")
async def admin_summary(days: int = 30, admin=Depends(admin_actor), db: AsyncSession = Depends(get_db)):
    from app.shop import orders, PAID_STATUSES
    days = max(1, min(days, 365))
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await db.execute(select(events.c.at, events.c.kind, events.c.visitor, events.c.path, events.c.ref,
                                    events.c.device, events.c.seller_id, events.c.detail)
                             .where(events.c.at >= since, events.c.kind != "search"))).all()
    per_day = defaultdict(lambda: {"views": 0, "visitors": set()})
    visitor_days = defaultdict(set)
    pages, refs, devices = defaultdict(int), defaultdict(int), defaultdict(int)
    shop_views, shop_contacts = defaultdict(int), defaultdict(int)
    contacts = 0
    for r in rows:
        d = _day(r.at)
        if r.kind == "view":
            per_day[d]["views"] += 1
            if r.visitor:
                per_day[d]["visitors"].add(r.visitor)
                visitor_days[r.visitor].add(d)
            pages[r.path or "/"] += 1
            if r.ref:
                refs[r.ref] += 1
            devices[r.device or "?"] += 1
            if r.seller_id:
                shop_views[r.seller_id] += 1
        elif r.kind == "contact":
            contacts += 1
            if r.seller_id:
                shop_contacts[r.seller_id] += 1
    series = []
    for i in range(days - 1, -1, -1):
        d = _day(datetime.utcnow() - timedelta(days=i))
        series.append({"day": d, "views": per_day[d]["views"], "visitors": len(per_day[d]["visitors"])})

    searches = (await db.execute(select(events.c.query, func.count(events.c.id), func.max(events.c.results))
                                 .where(events.c.at >= since, events.c.kind == "search")
                                 .group_by(events.c.query).order_by(func.count(events.c.id).desc()).limit(200))).all()
    top_searches = [{"query": q, "count": n} for q, n, best in searches if best][:20]
    not_found = [{"query": q, "count": n} for q, n, best in searches if not best][:20]

    ords = (await db.execute(select(orders).where(orders.c.created_at >= since))).all()
    paid = [o for o in ords if o.status in PAID_STATUSES]
    delivered = [o for o in ords if o.status == "delivered"]
    sales = round(sum(o.parts_total_sll for o in paid), 2)
    earned = round(sum(o.platform_fee_sll for o in delivered), 2)

    shop_orders = defaultdict(lambda: [0, 0.0])
    areas = defaultdict(lambda: [0, 0.0])
    pins = []
    for o in paid:
        shop_orders[o.seller_id][0] += 1
        shop_orders[o.seller_id][1] += o.parts_total_sll
    for o in ords:
        if o.status == "cancelled":
            continue
        a = (o.delivery_area or "Unknown").strip().title()
        areas[a][0] += 1
        areas[a][1] += o.parts_total_sll
        if o.delivery_lat is not None and o.delivery_lng is not None:
            pins.append([round(o.delivery_lat, 3), round(o.delivery_lng, 3)])

    seller_ids = set(shop_views) | set(shop_contacts) | set(shop_orders)
    names = {}
    if seller_ids:
        for b in (await db.execute(select(models.Business.owner_id, models.Business.name)
                                   .where(models.Business.owner_id.in_(seller_ids)))).all():
            names[b.owner_id] = b.name
        for u in (await db.execute(select(models.User.id, models.User.full_name).where(models.User.id.in_(seller_ids)))).all():
            names.setdefault(u.id, u.full_name)
    shops = sorted(({"seller_id": s, "name": names.get(s, f"Seller {s}"), "views": shop_views.get(s, 0),
                     "contacts": shop_contacts.get(s, 0), "orders": shop_orders[s][0] if s in shop_orders else 0,
                     "sales_sll": round(shop_orders[s][1], 2) if s in shop_orders else 0} for s in seller_ids),
                   key=lambda x: (-x["views"], -x["orders"]))[:20]

    visitors = len(visitor_days)
    checkout_visitors = len({r.visitor for r in rows if r.kind == "view" and r.path == "/checkout" and r.visitor})
    return {
        "days": days,
        "totals": {"visitors": visitors, "views": sum(p["views"] for p in series),
                   "returning": sum(1 for v in visitor_days.values() if len(v) > 1),
                   "contacts": contacts, "orders": len(ords), "paid_orders": len(paid), "delivered": len(delivered),
                   "sales_sll": sales, "commission_sll": earned},
        "funnel": [{"step": "Visited the site", "n": visitors}, {"step": "Reached checkout", "n": checkout_visitors},
                   {"step": "Placed an order", "n": len(ords)}, {"step": "Paid", "n": len(paid)},
                   {"step": "Delivered", "n": len(delivered)}],
        "series": series,
        "pages": sorted(({"path": p, "views": n} for p, n in pages.items()), key=lambda x: -x["views"])[:15],
        "sources": sorted(({"site": s, "visits": n} for s, n in refs.items()), key=lambda x: -x["visits"])[:10],
        "devices": dict(devices),
        "top_searches": top_searches, "not_found": not_found,
        "shops": shops,
        "areas": sorted(({"area": a, "orders": n, "sales_sll": round(v, 2)} for a, (n, v) in areas.items()),
                        key=lambda x: -x["orders"])[:20],
        "pins": pins[-500:],
    }


async def _seller_counts(db, seller_id, start, end):
    from app.shop import orders, PAID_STATUSES
    q = lambda kind: select(func.count(events.c.id)).where(events.c.seller_id == seller_id, events.c.kind == kind,
                                                           events.c.at >= start, events.c.at < end)
    views = (await db.execute(q("view"))).scalar() or 0
    contacts = (await db.execute(q("contact"))).scalar() or 0
    people = (await db.execute(select(func.count(distinct(events.c.visitor))).where(
        events.c.seller_id == seller_id, events.c.kind == "view", events.c.at >= start, events.c.at < end))).scalar() or 0
    ords = (await db.execute(select(orders.c.status, orders.c.parts_total_sll).where(
        orders.c.seller_id == seller_id, orders.c.created_at >= start, orders.c.created_at < end))).all()
    paid = [o for o in ords if o.status in PAID_STATUSES]
    return {"views": views, "people": people, "contacts": contacts, "orders": len(paid),
            "sales_sll": round(sum(o.parts_total_sll for o in paid), 2)}


@router.get("/seller/month")
async def seller_month(user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """'Your shop this month' and last month, for the seller."""
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev = (start - timedelta(days=1)).replace(day=1)
    top = (await db.execute(select(events.c.part_id, func.count(events.c.id)).where(
        events.c.seller_id == user.id, events.c.kind == "view", events.c.part_id.isnot(None), events.c.at >= start)
        .group_by(events.c.part_id).order_by(func.count(events.c.id).desc()).limit(5))).all()
    names = {}
    if top:
        for p in (await db.execute(select(models.PartListing.id, models.PartListing.name)
                                   .where(models.PartListing.id.in_([t[0] for t in top])))).all():
            names[p.id] = p.name
    return {"month": start.strftime("%B %Y"), "this_month": await _seller_counts(db, user.id, start, now + timedelta(seconds=1)),
            "last_month": await _seller_counts(db, user.id, prev, start),
            "top_parts": [{"part_id": pid, "name": names.get(pid, f"Part {pid}"), "views": n} for pid, n in top]}
