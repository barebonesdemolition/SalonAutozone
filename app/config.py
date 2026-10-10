# app/config.py
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    APP_NAME: str = "SalonAutoZone"
    APP_VERSION: str = "1.0.0"
    DEFAULT_CURRENCY: str = "SLL"

    DATABASE_URL: str = "sqlite+aiosqlite:///./saloncarparts.db"
    JWT_SECRET_KEY: str = "salon-autozone-secret-change-in-prod"

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.6-flash"
    AUTODEV_API_KEY: str = ""
    ADMIN_WHATSAPP: str = "23276570104"

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v):
        # Values pasted into a hosting dashboard often carry a trailing newline or
        # space; in GEMINI_MODEL that broke every AI request ("'\\n' in URL").
        return v.strip() if isinstance(v, str) else v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
