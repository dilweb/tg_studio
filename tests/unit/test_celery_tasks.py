"""Контракты celery-задач: acks_late + max_retries (сбой sweep'а не теряется молча)."""

from tg_studio.tasks import email_verification


def test_send_verification_email_acks_late():
    assert email_verification.send_verification_email_task.acks_late is True
