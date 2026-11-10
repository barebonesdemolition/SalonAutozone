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
    # Orange Money number sellers pay plan fees to. Set it to empty to hide it:
    # the pages then tell sellers to ask on WhatsApp instead.
    ORANGE_MONEY_NUMBER: str = "+232 76 570104"
    # Optional second mobile money number buyers can pay to. Empty hides it.
    AFRIMONEY_NUMBER: str = ""
    # Online payments through Monime (monime.io). Both must be set to show "Pay online":
    # the access token from Monime's dashboard (Developers > API keys) and the Space ID (spc-...).
    MONIME_ACCESS_TOKEN: str = ""
    MONIME_SPACE_ID: str = ""
    MONIME_API_BASE: str = "https://api.monime.io"
    MONIME_VERSION: str = "caph.2025-08-23"
    # Prices on the site are in old leones (SLL); Monime charges new leones (SLE).
    MONIME_SLL_PER_SLE: float = 1000
    # Emails (password reset codes) go out through Brevo's API. Both must be set:
    # BREVO_API_KEY from Brevo > SMTP & API > API keys, and EMAIL_FROM an address
    # verified in Brevo > Senders. Leave empty and resets use the WhatsApp link instead.
    BREVO_API_KEY: str = ""
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "SalonAutoZone"

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
