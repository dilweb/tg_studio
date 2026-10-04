from celery import Celery
from celery.schedules import crontab

from tg_studio.config import settings

celery_app = Celery(
    "tg_studio",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "tg_studio.tasks.email_verification",
        "tg_studio.tasks.session_offers",
        "tg_studio.tasks.prepay_expiry",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Almaty",
    enable_utc=True,
    beat_schedule={
        # Офферы без ответа сгорают и уходят следующему в очереди
        "expire-session-offers": {
            "task": "expire_session_offers",
            "schedule": crontab(minute="*/5"),
        },
        # Неоплаченные предоплаты отменяют бронь (дедлайн = жизнь счёта ApiPay)
        "expire-prepay-invoices": {
            "task": "expire_prepay_invoices",
            "schedule": crontab(minute="*/15"),
        },
    },
)
