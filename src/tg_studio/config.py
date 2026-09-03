from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    bot_token: str
    bot_username: str = ""  # без @, для ссылки регистрации мастера (t.me/bot_username?start=...)
    miniapp_url: str = "https://localhost:3000"

    # Database
    database_url: str = "postgresql+asyncpg://tg_studio:secret@localhost:5432/tg_studio"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Public URL for email verification links
    public_url: str = ""

    # LLM (OpenAI-compatible: OpenAI, Gemini, etc.)
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # JWT
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 30
    jwt_email_verify_expire_hours: int = 24

    # SMTP (Gmail: App Password at https://myaccount.google.com/apppasswords)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "TG Studio"

    # Google Calendar
    google_calendar_credentials_path: str = "credentials.json"
    google_calendar_redirect_uri: str = "http://localhost:8000/api/admin/google-calendar/callback"

    # App
    debug: bool = False
    allowed_hosts: str = "localhost"

    # HttpOnly JWT cookies (same-site browser clients)
    cookie_secure: bool = False  # True в проде за HTTPS (иначе браузер не пришлёт Secure-cookie)
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str = ""  # пусто = текущий хост; для поддоменов: .example.com

    @model_validator(mode="after")
    def _cookie_none_requires_secure(self) -> Self:
        if self.cookie_samesite == "none" and not self.cookie_secure:
            msg = "COOKIE_SAMESITE=none requires COOKIE_SECURE=true"
            raise ValueError(msg)
        return self


settings = Settings()
