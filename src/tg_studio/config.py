import logging
import secrets
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Значение-«маркер» из старых версий: если ключ остался дефолтным —
# подписи JWT и файловых токенов подделает любой, кто видел код.
_DEFAULT_JWT_SECRET = "change-me-in-production"


class AllowedUser(BaseModel):
    """Запись в ALLOWED_USERS: Telegram id → роль в панели.

    Env-значение — JSON-массив, pydantic-settings парсит его сам:
    ALLOWED_USERS=[{"id":228553615,"role":"owner"},{"id":222222222,"role":"master"}]
    """

    id: int  # telegram_id
    # admin — администратор студии (коворкинг, 05): назначается владельцем,
    # строка StudioAdmin в БД; роль в env — только для начальной загрузки
    role: Literal["owner", "master", "admin"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    bot_token: str
    miniapp_url: str = "https://localhost:3000"

    # Кто допущен в панель (миниаппа/дебаг): telegram id → роль.
    # Источник правды по доступу и ролям — окружение, БД только отражает его.
    allowed_users: list[AllowedUser] = []

    # Database
    database_url: str = "postgresql+asyncpg://tg_studio:secret@localhost:5432/tg_studio"

    # Локальное хранилище фото портфолио/аватаров: bind mount в контейнере.
    # Используется как fallback, если S3 не настроен.
    upload_dir: str = "/app/uploads"

    # S3/MinIO — файловое хранилище (портфолио, аватары).
    # Все четыре переменные заданы → файлы пишутся в бакет, метаданные
    # остаются в Postgres. Пусто → диск (upload_dir).
    s3_endpoint_url: str = ""  # напр. http://minio:9000
    s3_bucket: str = "tg-studio"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Public URL for email verification links
    public_url: str = ""

    # Публичный URL API (для абсолютных ссылок, напр. портфолио мастеров)
    api_public_url: str = ""

    # JWT. Пустой secret не принимаем: при пустом генерируем случайный
    # (локалка/тесты) и логируем предупреждение — иначе подписи подделает
    # любой, кто видел дефолт «change-me-in-production» из репозитория.
    jwt_secret_key: str = ""
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

    # Вебхук Telegram (нажатия inline-кнопок в напоминаниях, напр. «Подтвердить»
    # присутствие). Telegram шлёт его в X-Telegram-Bot-Api-Secret-Token;
    # регистрация — scripts/set_telegram_webhook.py. Пусто = вебхук выключен (503).
    telegram_webhook_secret: str = ""

    # Google Calendar
    google_calendar_credentials_path: str = "credentials.json"
    google_calendar_redirect_uri: str = "http://localhost:8000/api/admin/google-calendar/callback"

    # App
    debug: bool = False
    allowed_hosts: str = "localhost"
    # Сервис доступен снаружи по HTTPS (прод, ngrok, домен) — включаем жёсткий
    # режим автоматически. Локалка без https продолжает работать как раньше.
    require_https: bool | None = None

    # HttpOnly JWT cookies (same-site browser clients)
    cookie_secure: bool | None = None  # None = авто: True, если require_https
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str = ""  # пусто = текущий хост; для поддоменов: .example.com

    @model_validator(mode="after")
    def _cookie_none_requires_secure(self) -> Self:
        if self.cookie_samesite == "none" and not self.cookie_secure:
            msg = "COOKIE_SAMESITE=none requires COOKIE_SECURE=true"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _resolve_security_defaults(self) -> Self:
        if self.require_https is None:
            self.require_https = bool(
                (self.public_url or self.miniapp_url or self.api_public_url or "")
                .lower()
                .startswith("https://")
            )
        if self.cookie_secure is None:
            self.cookie_secure = self.require_https
        if self.cookie_samesite == "none" and not self.cookie_secure:
            msg = "COOKIE_SAMESITE=none requires COOKIE_SECURE=true"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _ensure_jwt_secret(self) -> Self:
        if not self.jwt_secret_key:
            self.jwt_secret_key = secrets.token_urlsafe(48)
            logger.warning(
                "JWT_SECRET_KEY не задан — сгенерирован случайный. Сессии и "
                "файловые токены протухнут после рестарта; задайте ключ в .env."
            )
        elif self.jwt_secret_key == _DEFAULT_JWT_SECRET:
            self.jwt_secret_key = secrets.token_urlsafe(48)
            logger.warning(
                "JWT_SECRET_KEY равен дефолтному %r — заменён случайным. "
                "Все выданные токены и подписанные ссылки ?t= невалидны; "
                "задайте свой ключ в .env.",
                _DEFAULT_JWT_SECRET,
            )
        return self

    @property
    def cors_allowed_origins(self) -> list[str]:
        """Явные origin'ы для CORS (credentials + «*» несовместимы — браузер
        отбросит ответ). Берём хосты публичных URL; пусто = same-origin only."""
        origins = []
        for url in (self.public_url, self.miniapp_url, self.api_public_url):
            if not url:
                continue
            parts = urlsplit(url)
            if parts.scheme and parts.netloc:
                origin = f"{parts.scheme}://{parts.netloc}"
                if origin not in origins:
                    origins.append(origin)
        return origins

    @property
    def trusted_hosts(self) -> list[str]:
        """Host-заголовки, которые принимает API (Host-header poisoning).
        Пустой список = без ограничений (локальная разработка)."""
        if not self.require_https:
            return []
        hosts = {urlsplit(u).netloc for u in (self.public_url, self.api_public_url) if u}
        hosts = {h for h in hosts if h}
        return sorted(hosts)

    @property
    def s3_enabled(self) -> bool:
        return bool(
            self.s3_endpoint_url and self.s3_bucket and self.s3_access_key and self.s3_secret_key
        )


settings = Settings()
