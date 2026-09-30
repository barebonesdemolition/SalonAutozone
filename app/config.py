# app/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Salon AutoZone & AutoTrader"
    APP_VERSION: str = "1.0.0"
    DEFAULT_CURRENCY: str = "SLL"

    DATABASE_URL: str = "sqlite+aiosqlite:///./saloncarparts.db"
    JWT_SECRET_KEY: str = "salon-autozone-secret-change-in-prod"

    GEMINI_API_KEY: str = ""
    ADMIN_WHATSAPP: str = "23276570104"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
