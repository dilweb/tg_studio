from celery import Celery
from celery.schedules import crontab

from tg_studio.config import settings

celery_app = Celery(
    "tg_studio",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "tg_studio.tasks.email_verification",
        "tg_studio.tasks.booking_holds",
        "tg_studio.tasks.booking_reminders",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Almaty",
    enable_utc=True,
    beat_schedule={
        # Истёкший hold брони места (ожидает_оплаты) → истекла_оплата, место свободно
        "expire-booking-holds": {
            "task": "expire_booking_holds",
            "schedule": crontab(minute="*/5"),
        },
        # Напоминания мастерам: «оплатите до …» и «скоро бронь» с кнопкой «Подтвердить»
        "send-booking-reminders": {
            "task": "send_booking_reminders",
            "schedule": crontab(minute="*/5"),
        },
    },
)
