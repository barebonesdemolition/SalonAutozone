import os

# Tests run against a throwaway SQLite file with demo data.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_saloncarparts.db")
os.environ.setdefault("AUTO_CREATE_TABLES", "true")
os.environ.setdefault("SEED_DEMO_DATA", "true")
os.environ.setdefault("ADMIN_SECRET", "test-admin-secret")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
