"""Удаление событий сеансов из Google Calendar (календарь — зеркало БД).

Вынесено в отдельный модуль, чтобы и API тату-модуля, и sweeps платежей
(истечение неоплаченной предоплаты) могли освобождать слот в календаре
при отмене брони.
"""

import logging

from tg_studio.db.models import Business, Master, TattooSession
from tg_studio.modules.google_calendar.client import cancel_event

logger = logging.getLogger(__name__)


async def remove_session_event(
    tattoo_session: TattooSession,
    master: Master | None,
    business: Business,
) -> bool:
    """Удалить событие сеанса из Google Calendar.

    True — события нет или оно удалено (слот в календаре свободен);
    False — удалить не удалось (нет мастера/credentials или сбой API).
    Саму колонку google_event_id здесь НЕ трогаем: вызывающий очищает её
    только при True, иначе событие в календаре могло остаться и попытку
    стоит повторить позже.
    """
    if not tattoo_session.google_event_id:
        return True
    if master is None or not business.google_calendar_credentials_json:
        return False
    removed = await cancel_event(
        credentials_json=business.google_calendar_credentials_json,
        event_id=tattoo_session.google_event_id,
        calendar_id=master.google_calendar_id or "primary",
    )
    if not removed:
        logger.warning(
            "Не удалось удалить событие %s сеанса %d из Google Calendar",
            tattoo_session.google_event_id,
            tattoo_session.id,
        )
    return removed
