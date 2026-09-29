"""
Add performance indexes to the database.

Run from project root:
    python -m app.add_indexes

Optional flags:
    --dry-run     Print SQL statements without executing them
    --verbose     Show timing for each index
"""
import asyncio
import os
import sys
import time

# Ensure project root is on sys.path so "app.*" imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from app.db import engine


# ============================================================
# INDEX DEFINITIONS
# Grouped by domain. Each entry is (label, sql).
# ============================================================
INDEXES = [
    # ---------- Extensions ----------
    ("ext:pg_trgm",
     "CREATE EXTENSION IF NOT EXISTS pg_trgm"),

    # ---------- Vehicle listings ----------
    ("idx_vehicle_created_at",
     "CREATE INDEX IF NOT EXISTS idx_vehicle_created_at "
     "ON vehicle_listings (created_at DESC)"),

    ("idx_vehicle_make_model",
     "CREATE INDEX IF NOT EXISTS idx_vehicle_make_model "
     "ON vehicle_listings (make, model)"),

    ("idx_vehicle_location_created",
     "CREATE INDEX IF NOT EXISTS idx_vehicle_location_created "
     "ON vehicle_listings (location, created_at DESC)"),

    ("idx_vehicle_seller_created",
     "CREATE INDEX IF NOT EXISTS idx_vehicle_seller_created "
     "ON vehicle_listings (seller_id, created_at DESC)"),

    ("idx_vehicle_title_trgm",
     "CREATE INDEX IF NOT EXISTS idx_vehicle_title_trgm "
     "ON vehicle_listings USING gin (title gin_trgm_ops)"),

    # ---------- Part listings ----------
    ("idx_part_created_at",
     "CREATE INDEX IF NOT EXISTS idx_part_created_at "
     "ON part_listings (created_at DESC)"),

    ("idx_part_category_created",
     "CREATE INDEX IF NOT EXISTS idx_part_category_created "
     "ON part_listings (category, created_at DESC)"),

    ("idx_part_make_model",
     "CREATE INDEX IF NOT EXISTS idx_part_make_model "
     "ON part_listings (compatible_make, compatible_model)"),

    ("idx_part_location_created",
     "CREATE INDEX IF NOT EXISTS idx_part_location_created "
     "ON part_listings (location, created_at DESC)"),

    ("idx_part_compatible_make",
     "CREATE INDEX IF NOT EXISTS idx_part_compatible_make "
     "ON part_listings (compatible_make)"),

    ("idx_part_compatible_model",
     "CREATE INDEX IF NOT EXISTS idx_part_compatible_model "
     "ON part_listings (compatible_model)"),

    ("idx_part_vendor_created",
     "CREATE INDEX IF NOT EXISTS idx_part_vendor_created "
     "ON part_listings (vendor_id, created_at DESC)"),

    ("idx_part_name_trgm",
     "CREATE INDEX IF NOT EXISTS idx_part_name_trgm "
     "ON part_listings USING gin (name gin_trgm_ops)"),

    # ---------- Orders ----------
    ("idx_order_created_at",
     "CREATE INDEX IF NOT EXISTS idx_order_created_at "
     "ON orders (created_at DESC)"),

    ("idx_order_buyer_created",
     "CREATE INDEX IF NOT EXISTS idx_order_buyer_created "
     "ON orders (buyer_id, created_at DESC)"),

    # ---------- Import requests ----------
    ("idx_import_created_at",
     "CREATE INDEX IF NOT EXISTS idx_import_created_at "
     "ON import_requests (created_at DESC)"),

    ("idx_import_status_created",
     "CREATE INDEX IF NOT EXISTS idx_import_status_created "
     "ON import_requests (status, created_at DESC)"),

    # ---------- Supplier catalog ----------
    ("idx_catalog_created_at",
     "CREATE INDEX IF NOT EXISTS idx_catalog_created_at "
     "ON supplier_catalog (created_at DESC)"),

    ("idx_catalog_category_created",
     "CREATE INDEX IF NOT EXISTS idx_catalog_category_created "
     "ON supplier_catalog (category, created_at DESC)"),

    # ---------- Inquiries ----------
    ("idx_inquiry_created_at",
     "CREATE INDEX IF NOT EXISTS idx_inquiry_created_at "
     "ON inquiries (created_at DESC)"),

    ("idx_inquiry_seller_created",
     "CREATE INDEX IF NOT EXISTS idx_inquiry_seller_created "
     "ON inquiries (seller_id, created_at DESC)"),

    ("idx_inquiry_listing",
     "CREATE INDEX IF NOT EXISTS idx_inquiry_listing "
     "ON inquiries (listing_type, listing_id)"),
]


# ============================================================
# RUNNER
# ============================================================
async def main():
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    verbose = "--verbose" in args

    if dry_run:
        print("🔍 DRY RUN — printing SQL only\n")
        for label, sql in INDEXES:
            print(f"-- {label}")
            print(f"{sql};\n")
        return

    total = len(INDEXES)
    applied = 0
    skipped = 0

    print(f"Connecting to database... ({total} indexes to check)\n")

    async with engine.begin() as conn:
        for i, (label, sql) in enumerate(INDEXES, 1):
            t0 = time.perf_counter()
            try:
                await conn.execute(text(sql))
                elapsed_ms = (time.perf_counter() - t0) * 1000
                applied += 1
                if verbose:
                    print(f"✅ [{i}/{total}] {label} ({elapsed_ms:.0f}ms)")
                else:
                    print(f"✅ {label}")
            except Exception as e:
                skipped += 1
                short = str(e).split("\n")[0][:120]
                print(f"⚠️  {label} — skipped ({short})")

    print(f"\n🎉 Done. Applied: {applied}, Skipped: {skipped}, Total: {total}")


if __name__ == "__main__":
    asyncio.run(main())
