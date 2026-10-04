"""Распределение записей между мастерами: очередь офферов.

Работа создаётся без мастера (master_id = NULL) и раздаётся по очереди:
никакого «кто первый успел» — принять может только тот, кому оффер
предложен прямо сейчас. Ранжирование детерминированное:

1. Специализация: стиль работы ∈ master.specializations (пустой список =
   универсал — подходит под любой стиль). Если под стиль не подходит
   никто — очередь из всех активных мастеров.
2. Загрузка мастера за календарный месяц сеанса, в часах (длительность
   сеанса в БД не хранится — берём дефолт мастера, 60 мин если не задан).
   Меньше часов — выше в очереди.
3. Round-robin как tie-break: меньше работ за всё время, затем id.

Все офферы создаются сразу (rank = позиция), pending только у первого.
Отказ/таймаут → следующий в очереди; очередь пуста → эскалация владельцу
в Telegram. Таймаут ответа — OFFER_TIMEOUT, закрывает celery-задача.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from tg_studio.config import settings
from tg_studio.db.models import (
    Business,
    Client,
    Master,
    SessionOffer,
    SessionOfferStatus,
    TattooProjectStatus,
    TattooSession,
    TattooWork,
    User,
)
from tg_studio.modules.chat.service import resolve_owner_telegram_id, send_text_to_telegram

logger = logging.getLogger(__name__)

# Сколько минут мастер может думать над оффером. Дальше оффер сгорает
# (celery-задача) и уходит следующему в очереди.
OFFER_TIMEOUT = timedelta(minutes=30)

# Длительность по умолчанию для расчёта загрузки, если у мастера не задана
DEFAULT_SESSION_MINUTES = 60


def _month_bounds(first_start: datetime) -> tuple[datetime, datetime]:
    """Календарный месяц сеанса: [первый день, первый день следующего)."""
    start = first_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


async def rank_masters(
    session, business: Business, style: str, first_start: datetime
) -> list[tuple[Master, float]]:
    """Мастера по убыванию приоритета: (мастер, загрузка за месяц в часах).

    Сначала те, у кого стиль в специализациях (пустой список = универсал);
    если таких нет — все активные мастера бизнеса.
    """
    masters = (
        await session.execute(
            select(Master).where(
                Master.business_id == business.id,
                Master.is_active.is_(True),
            )
        )
    ).scalars().all()

    fitting = [m for m in masters if not m.specializations or style in (m.specializations or [])]
    pool = fitting or masters

    month_start, month_end = _month_bounds(first_start)
    # Сеансы месяца по мастерам (отменённые не грузят)
    load_rows = (
        await session.execute(
            select(TattooWork.master_id, func.count(TattooSession.id))
            .join(TattooSession, TattooSession.work_id == TattooWork.id)
            .where(
                TattooWork.business_id == business.id,
                TattooWork.master_id.isnot(None),
                TattooSession.session_date >= month_start,
                TattooSession.session_date < month_end,
                TattooSession.status != TattooProjectStatus.cancelled.value,
            )
            .group_by(TattooWork.master_id)
        )
    ).all()
    session_counts = {master_id: count for master_id, count in load_rows}

    # Round-robin tie-break: сколько работ у мастера вообще
    work_rows = (
        await session.execute(
            select(TattooWork.master_id, func.count())
            .where(
                TattooWork.business_id == business.id,
                TattooWork.master_id.isnot(None),
            )
            .group_by(TattooWork.master_id)
        )
    ).all()
    work_counts = {master_id: count for master_id, count in work_rows}

    def load_hours(m: Master) -> float:
        minutes = m.default_duration_minutes or DEFAULT_SESSION_MINUTES
        return session_counts.get(m.id, 0) * minutes / 60

    ranked = sorted(
        pool,
        key=lambda m: (load_hours(m), work_counts.get(m.id, 0), m.id),
    )
    return [(m, load_hours(m)) for m in ranked]


async def create_offers(session, work: TattooWork, first_start: datetime) -> list[SessionOffer]:
    """Очередь офферов работы. Пустой список — некому предложить (эскалация)."""
    business = await session.get(Business, work.business_id)
    ranked = await rank_masters(session, business, work.style, first_start)
    offers: list[SessionOffer] = []
    for rank, (master, _hours) in enumerate(ranked):
        offer = SessionOffer(
            business_id=work.business_id,
            work_id=work.id,
            master_id=master.id,
            rank=rank,
            status=SessionOfferStatus.pending.value if rank == 0 else SessionOfferStatus.queued.value,
            expires_at=datetime.now(first_start.tzinfo or UTC) + OFFER_TIMEOUT if rank == 0 else None,
        )
        session.add(offer)
        offers.append(offer)
    await session.flush()
    return offers


async def advance_queue(session, work: TattooWork) -> SessionOffer | None:
    """Следующий оффер в очереди → pending. None — очередь пуста."""
    if work.master_id is not None:
        return None
    next_offer = (
        await session.execute(
            select(SessionOffer)
            .where(
                SessionOffer.work_id == work.id,
                SessionOffer.status == SessionOfferStatus.queued.value,
            )
            .order_by(SessionOffer.rank.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if next_offer is None:
        return None
    next_offer.status = SessionOfferStatus.pending.value
    next_offer.expires_at = datetime.now(UTC) + OFFER_TIMEOUT
    return next_offer


async def close_remaining_offers(session, work: TattooWork, except_offer_id: int) -> None:
    """Работа распределена — остальные офферы закрываются."""
    await session.execute(
        update(SessionOffer)
        .where(
            SessionOffer.work_id == work.id,
            SessionOffer.id != except_offer_id,
            SessionOffer.status.in_(
                [SessionOfferStatus.queued.value, SessionOfferStatus.pending.value]
            ),
        )
        .values(
            status=SessionOfferStatus.closed.value,
            responded_at=datetime.now(UTC),
        )
    )


# ---------------------------------------------------------------------------
# Telegram-уведомления (best-effort: сбой доставки не ломает распределение)
# ---------------------------------------------------------------------------


async def _master_telegram_id(session, master: Master) -> int | None:
    if master.telegram_id:
        return master.telegram_id
    if master.user_id:
        user = await session.get(User, master.user_id)
        if user is not None and user.telegram_id:
            return user.telegram_id
    return None


def offer_text(work: TattooWork, client_name: str, session_start: datetime, price) -> str:
    """Сообщение мастеру о новой записи в очереди."""
    price_part = f"\nОриентир по цене: {float(price):,.0f} ₸" if price is not None else ""
    base = f"{settings.miniapp_url}".rstrip("/")
    return (
        f"🔔 Новая запись в очереди\n"
        f"Клиент: {client_name}\n"
        f"Стиль: {work.style}\n"
        f"Размер: {work.size_length_cm:g}×{work.size_height_cm:g} см, "
        f"сложность: {work.complexity}\n"
        f"Зона: {work.placement}\n"
        f"Сеанс: {session_start:%d.%m.%Y %H:%M}{price_part}\n"
        f"Подтверди или откажись в миниаппе: {base}"
    )


async def notify_master(session, offer: SessionOffer) -> None:
    """Оффер мастеру в Telegram. best-effort: не стартовал бота — не беда."""
    master = await session.get(Master, offer.master_id)
    if master is None:
        return
    telegram_id = await _master_telegram_id(session, master)
    if not telegram_id:
        logger.info("Master %s has no telegram — offer %s stays pending", master.id, offer.id)
        return
    work = await session.get(TattooWork, offer.work_id)
    client = await session.get(Client, work.client_id)
    first_session = (
        await session.execute(
            select(TattooSession)
            .where(TattooSession.work_id == work.id)
            .order_by(TattooSession.session_date.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    try:
        await send_text_to_telegram(
            telegram_id,
            offer_text(
                work,
                client.full_name if client else "клиент",
                first_session.session_date,
                first_session.recommended_price if first_session else None,
            ),
        )
    except Exception:
        logger.exception("Failed to notify master %s about offer %s", master.id, offer.id)


async def escalate_to_owner(session, work: TattooWork) -> None:
    """Все мастера отказались/не ответили — запись на решение владельца."""
    owner_tg_id = await resolve_owner_telegram_id(session)
    if owner_tg_id is None:
        logger.warning("No owner to escalate work %s", work.id)
        return
    client = await session.get(Client, work.client_id)
    first_session = (
        await session.execute(
            select(TattooSession)
            .where(TattooSession.work_id == work.id)
            .order_by(TattooSession.session_date.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    when = f"{first_session.session_date:%d.%m.%Y %H:%M}" if first_session else "дата не задана"
    text = (
        f"⚠️ Никто не взял запись\n"
        f"Клиент: {client.full_name if client else '?'}\n"
        f"Стиль: {work.style}, зона: {work.placement}\n"
        f"Сеанс: {when}\n"
        f"Реши сам: назначь мастера в миниаппе или перенеси."
    )
    try:
        await send_text_to_telegram(owner_tg_id, text)
    except Exception:
        logger.exception("Failed to escalate work %s to owner", work.id)


# ---------------------------------------------------------------------------
# Таймаут (celery beat): pending офферы без ответа → expired → очередь дальше
# ---------------------------------------------------------------------------


async def expire_stale_offers(session) -> int:
    """Сгоревшие офферы → expired, очередь продвигается. Возвращает число."""
    stale = (
        await session.execute(
            select(SessionOffer).where(
                SessionOffer.status == SessionOfferStatus.pending.value,
                SessionOffer.expires_at.isnot(None),
                SessionOffer.expires_at < datetime.now().astimezone(),
            )
        )
    ).scalars().all()
    for offer in stale:
        offer.status = SessionOfferStatus.expired.value
        offer.responded_at = datetime.now().astimezone()
        work = await session.get(TattooWork, offer.work_id)
        if work is None or work.master_id is not None:
            continue
        next_offer = await advance_queue(session, work)
        if next_offer is None:
            await escalate_to_owner(session, work)
        else:
            await notify_master(session, next_offer)
    if stale:
        await session.commit()
    return len(stale)


def run_expire_stale_offers() -> int:
    """Синхронная обёртка для celery-воркера."""
    from tg_studio.db.session import async_session_factory

    async def _run() -> int:
        async with async_session_factory() as db_session:
            return await expire_stale_offers(db_session)

    return asyncio.run(_run())
