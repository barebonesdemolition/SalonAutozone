"""One-time switch of stored money from old leones (SLL) to new leones (Le / SLE).

Sierra Leone redenominated in 2022: 1 new leone = 1,000 old leones. The site used to store
and show old leones. From now on every amount is new leones, so existing amounts are divided
by 1,000 exactly once. A row in app_keys records that it was done; the flag and the changes
are written in one transaction, so a crash can't leave it half done or let it run twice.

Column names keep their "_sll" ending; only the unit changed.
"""
import logging

from sqlalchemy import inspect as sa_inspect, select, text

from app.db import engine
from app.notify import _md as notify_md, app_keys

log = logging.getLogger(__name__)

FLAG = "money_unit"
# table -> money columns
MONEY_COLUMNS = {
    "businesses": ["okada_fee_sll", "van_fee_sll"],
    "vehicle_listings": ["price_sll"],
    "part_listings": ["price_sll"],
    "supplier_catalog": ["price_sll"],
    "import_requests": ["budget_sll", "quoted_price_sll"],
    "saved_listings": ["listing_price_sll"],
    "plans": ["price_sll"],
    "payments": ["amount_sll"],
    "shop_orders": ["parts_total_sll", "delivery_fee_sll", "platform_fee_sll", "driver_payout_sll", "shop_payout_sll"],
    "shop_order_items": ["unit_price_sll"],
    "delivery_jobs": ["fee_sll", "payout_sll"],
    "order_payments": ["amount_sll"],
    "refunds": ["amount_sll"],
}
# delivery_settings is key/value; only these keys are money
MONEY_SETTINGS = ["okada_fee_sll", "van_fee_sll"]


async def convert_to_new_leones():
    """Divide stored amounts by 1,000 if that hasn't been done on this database yet."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: notify_md.create_all(c, tables=[app_keys], checkfirst=True))
            done = (await conn.execute(select(app_keys.c.value).where(app_keys.c.name == FLAG))).scalar()
            if done == "SLE":
                return False
            # Claim it first: a second worker starting at the same time fails here and rolls back.
            await conn.execute(app_keys.insert().values(name=FLAG, value="SLE"))

            def present(sync_conn):
                insp = sa_inspect(sync_conn)
                tables = set(insp.get_table_names())
                found = {}
                for table, cols in MONEY_COLUMNS.items():
                    if table in tables:
                        have = {c["name"] for c in insp.get_columns(table)}
                        found[table] = [c for c in cols if c in have]
                return found, "delivery_settings" in tables

            found, has_settings = await conn.run_sync(present)
            changed = 0
            for table, cols in found.items():
                for col in cols:
                    res = await conn.execute(text(
                        f"UPDATE {table} SET {col} = ROUND(CAST({col} AS NUMERIC) / 1000, 2) WHERE {col} IS NOT NULL AND {col} <> 0"))
                    changed += res.rowcount or 0
            if has_settings:
                for key in MONEY_SETTINGS:
                    await conn.execute(text(
                        "UPDATE delivery_settings SET value = ROUND(CAST(value AS NUMERIC) / 1000, 2) WHERE key = :k AND value IS NOT NULL"),
                        {"k": key})
        log.warning("Money converted to new leones (divided by 1,000): %s values", changed)
        return True
    except Exception:
        log.exception("Could not convert money to new leones; will try again on next start")
        return False
