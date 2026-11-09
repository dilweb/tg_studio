"""Контракты celery-задач: acks_late + max_retries (сбой sweep'а не теряется молча)."""

from tg_studio.tasks import email_verification, prepay_expiry, session_offers


def test_expire_session_offers_contract():
    task = session_offers.expire_session_offers_task
    assert task.acks_late is True
    assert task.max_retries == 3


def test_expire_prepay_invoices_contract():
    task = prepay_expiry.expire_prepay_invoices_task
    assert task.acks_late is True
    assert task.max_retries == 3


def test_send_verification_email_acks_late():
    assert email_verification.send_verification_email_task.acks_late is True
