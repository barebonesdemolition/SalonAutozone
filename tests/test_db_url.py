"""Hosted Postgres addresses (Neon, Supabase) work with the async driver."""
from app.db import split_async_url


def test_neon_url():
    url, args = split_async_url("postgresql+asyncpg://u:p@ep-cool-1.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
    assert url == "postgresql+asyncpg://u:p@ep-cool-1.eu-central-1.aws.neon.tech/neondb"
    assert args == {"ssl": "require"}


def test_neon_pooled_url():
    url, args = split_async_url("postgresql+asyncpg://u:p@ep-cool-1-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require")
    assert "sslmode" not in url and args == {"ssl": "require", "statement_cache_size": 0}


def test_other_urls_unchanged():
    for u in ("postgresql+asyncpg://u:p@localhost:5432/db", "sqlite+aiosqlite:///./x.db"):
        assert split_async_url(u) == (u, {})
    url, args = split_async_url("postgresql+asyncpg://u:p@h/db?sslmode=disable&application_name=saz")
    assert url == "postgresql+asyncpg://u:p@h/db?application_name=saz" and args == {}
