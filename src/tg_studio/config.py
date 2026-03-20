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

    # Kaspi Pay (deprecated, оставлено для обратной совместимости callback)
    kaspi_merchant_id: str = ""
    kaspi_api_key: str = ""
    kaspi_callback_secret: str = ""
    kaspi_api_url: str = "https://pay.kaspi.kz/pay/109/paymentorder"

    # Freedom Pay
    freedom_pay_api_url: str = "https://api.freedompay.kz"
    api_public_url: str = ""  # Базовый URL API для pg_result_url (например https://api.example.com)

    # App
    debug: bool = False
    allowed_hosts: str = "localhost"


settings = Settings()
