"""
One-off script to add indexes to existing tables.
Safe to run multiple times — uses IF NOT EXISTS.
"""
import asyncio
from sqlalchemy import text
from app.db import engine


INDEXES = [
    # ---------- Vehicle listings ----------
    "CREATE INDEX IF NOT EXISTS idx_vehicle_created_at ON vehicle_listings (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_vehicle_make_model ON vehicle_listings (make, model)",
    "CREATE INDEX IF NOT EXISTS idx_vehicle_location_created ON vehicle_listings (location, created_at DESC)",

    # ---------- Part listings ----------
    "CREATE INDEX IF NOT EXISTS idx_part_created_at ON part_listings (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_part_category_created ON part_listings (category, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_part_make_model ON part_listings (compatible_make, compatible_model)",
    "CREATE INDEX IF NOT EXISTS idx_part_location_created ON part_listings (location, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_part_compatible_make ON part_listings (compatible_make)",
    "CREATE INDEX IF NOT EXISTS idx_part_compatible_model ON part_listings (compatible_model)",

    # ---------- Orders ----------
    "CREATE INDEX IF NOT EXISTS idx_order_created_at ON orders (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_order_buyer_created ON orders (buyer_id, created_at DESC)",

    # ---------- Import requests ----------
    "CREATE INDEX IF NOT EXISTS idx_import_created_at ON import_requests (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_import_status_created ON import_requests (status, created_at DESC)",

    # ---------- Supplier catalog ----------
    "CREATE INDEX IF NOT EXISTS idx_catalog_created_at ON supplier_catalog (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_category_created ON supplier_catalog (category, created_at DESC)",

    # ---------- Inquiries ----------
    "CREATE INDEX IF NOT EXISTS idx_inquiry_created_at ON inquiries (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_inquiry_seller_created ON inquiries (seller_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_inquiry_listing ON inquiries (listing_type, listing_id)",
]


async def main():
    async with engine.begin() as conn:
        for sql in INDEXES:
            try:
                await conn.execute(text(sql))
                print(f"✅ {sql.split(' ON ')[0].replace('CREATE INDEX IF NOT EXISTS ', '')}")
            except Exception as e:
                print(f"⚠️  Skipped: {sql[:60]}... ({e})")
    print("\n🎉 All indexes created.")


if __name__ == "__main__":
    asyncio.run(main())
