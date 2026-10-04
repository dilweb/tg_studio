from typing import Literal, Self

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AllowedUser(BaseModel):
    """Запись в ALLOWED_USERS: Telegram id → роль в панели.

    Env-значение — JSON-массив, pydantic-settings парсит его сам:
    ALLOWED_USERS=[{"id":228553615,"role":"owner"},{"id":222222222,"role":"master"}]
    """

    id: int  # telegram_id
    role: Literal["owner", "master"]


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

    # Кто допущен в панель (миниаппа/дебаг): telegram id → роль.
    # Источник правды по доступу и ролям — окружение, БД только отражает его.
    allowed_users: list[AllowedUser] = []

    # Database
    database_url: str = "postgresql+asyncpg://tg_studio:secret@localhost:5432/tg_studio"

    # Локальное хранилище фото сеансов (tattoo-модуль): bind mount в контейнере
    upload_dir: str = "/app/uploads"

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

    # Apipay (Kaspi Pay API, api.apipay.kz): приём оплаты счётом по номеру.
    # Ключ вводится в окружение (APIPAY_API_KEY), никогда не в чат/репозиторий.
    # Пусто = приём платежей только manual (наличные/перевод, кнопка «Оплатил»).
    apipay_api_key: str = ""
    # Секрет проверки подписи вебхуков (X-Webhook-Signature) — при подключении
    apipay_webhook_secret: str = ""
    apipay_base_url: str = "https://api.apipay.kz/api/v1"

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
