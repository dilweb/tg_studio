from celery import Celery

from tg_studio.config import settings

celery_app = Celery(
    "tg_studio",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["tg_studio.tasks.notifications", "tg_studio.tasks.reminders", "tg_studio.tasks.expire_payment"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Almaty",
    enable_utc=True,
    beat_schedule={
        "schedule-reminders-every-15min": {
            "task": "schedule_reminders",
            "schedule": 900,  # каждые 15 минут
        },
    },
)
