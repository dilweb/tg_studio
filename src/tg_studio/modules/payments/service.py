"""Баланс тату-работы: считается, никогда не хранится.

Хранимые факты — TattooWork.contract_price и платежи (status=paid).
Все «сколько осталось», «можно ли закрывать работу» — вычисление в момент
запроса, поэтому журнал не может разойтись с балансом.

Здесь же — предоплата брони: мастер подтвердил работу (принял оффер) →
автосчёт на prepay_percent от цены; не оплачен за 24ч (жизнь счёта ApiPay)
→ бронь авто-отменяется, слот освобождается, обе стороны уведомлены.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import func, select

from tg_studio.db.locks import for_update
from tg_studio.db.models import (
    Business,
    Client,
    Master,
    Payment,
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
    TattooProjectStatus,
    TattooSession,
    TattooWork,
    TattooWorkStatus,
    User,
)
from tg_studio.modules.chat.service import resolve_owner_telegram_id, send_text_to_telegram
from tg_studio.modules.payments.providers import (
    InvoiceContext,
    PaymentProviderError,
    apipay_configured,
    get_provider,
)
from tg_studio.modules.tattoo.calendar import remove_session_event

logger = logging.getLogger(__name__)

# Дедлайн оплаты предоплаты = жизнь счёта ApiPay (24ч)
PREPAY_TTL = timedelta(hours=24)


def _dec(value) -> Decimal:
    return Decimal(str(value))


async def paid_total(session, work_id: int) -> Decimal:
    """Σ оплаченных платежей работы (pending/cancelled/expired не считаются)."""
    result = await session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.work_id == work_id,
            Payment.status == PaymentStatus.paid.value,
        )
    )
    return _dec(result.scalar_one())


def balance_of(contract_price, paid_total: Decimal) -> Decimal | None:
    """Остаток долга по работе; None — цена ещё не зафиксирована."""
    if contract_price is None:
        return None
    return _dec(contract_price) - paid_total


async def work_balance(session, work: TattooWork) -> tuple[Decimal | None, Decimal]:
    """(баланс, оплачено) для работы. balance None — счёт ещё не заведён."""
    paid = await paid_total(session, work.id)
    return balance_of(work.contract_price, paid), paid


async def ensure_can_complete(session, work: TattooWork) -> None:
    """Работу нельзя закрывать с непогашенным балансом.

    Кейс «договорились, что за половину доплатит потом» решается
    пересмотром contract_price (скидка/договорённость), а не обходом этого
    запрета: молча потерять часть цены нельзя.
    """
    balance, _paid = await work_balance(session, work)
    if balance is None or balance <= 0:
        return
    raise HTTPException(
        status_code=409,
        detail=f"Работа закрыта не полностью: остаток {balance:,.0f} ₸ не оплачен"
        " — сначала доплата или пересмотр цены",
    )


def normalize_phone(raw: str | None) -> str | None:
    """Телефон клиента к формату ApiPay 8XXXXXXXXXX. None — не приводится.

    +7XXXXXXXXXX → 8XXXXXXXXXX, цифры из «8 (777) 123-45-67» склеиваются.
    """
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits.startswith("7"):
        digits = "8" + digits[1:]
    if len(digits) == 10 and digits.startswith("7"):
        digits = "8" + digits
    return digits if len(digits) == 11 and digits.startswith("8") else None


# ---------------------------------------------------------------------------
# Предоплата брони
# ---------------------------------------------------------------------------


async def contract_price_for_work(session, work: TattooWork) -> Decimal | None:
    """Σ recommended_price сеансов — база для фиксации договорной цены."""
    result = await session.execute(
        select(func.coalesce(func.sum(TattooSession.recommended_price), 0)).where(
            TattooSession.work_id == work.id,
            TattooSession.recommended_price.isnot(None),
        )
    )
    total = _dec(result.scalar_one())
    return total if total > 0 else None


async def prepay_percent(business: Business) -> Decimal:
    """Процент предоплаты из pricing_config бизнеса (30 по умолчанию)."""
    cfg = business.pricing_config if isinstance(business.pricing_config, dict) else {}
    try:
        return Decimal(str(cfg.get("prepay_percent", 30)))
    except Exception:
        return Decimal("30")


async def _first_session(session, work_id: int) -> TattooSession | None:
    return (
        await session.execute(
            select(TattooSession)
            .where(TattooSession.work_id == work_id)
            .order_by(TattooSession.session_date.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def create_prepay_invoice(
    session, work: TattooWork, business: Business, user: User
) -> Payment | None:
    """Автосчёт на предоплату при подтверждении работы мастером.

    Цена: work.contract_price, а если не зафиксирована — Σ recommended_price
    сеансов (фиксируем). Сумма счёта — prepay_percent из «Бизнеса» (30%),
    целые тенге. Провайдер: apipay, если настроен и у клиента телефон;
    иначе manual pending — мастер подтвердит оплату сам. Сбой ApiPay не
    срывает подтверждение работы: тихо падаем в manual.

    None — счёта нет (цена 0 или неизвестна): бронь живёт без предоплаты.

    Идемпотентен: повторный вызов (ретрай запроса, параллельные транзакции)
    возвращает уже созданный pending-счёт, а не плодит дубли. Платёж
    коммитится ДО вызова провайдера: сбой ApiPay переводит счёт в manual,
    а счёт в ApiPay без записи в БД (orphan) не создаётся.
    """
    existing = (
        await session.execute(
            for_update(
                select(Payment).where(
                    Payment.work_id == work.id,
                    Payment.kind == PaymentKind.prepay.value,
                    Payment.status == PaymentStatus.pending.value,
                ),
                session,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    price = _dec(work.contract_price) if work.contract_price is not None else None
    if price is None:
        price = await contract_price_for_work(session, work)
        if price is not None:
            work.contract_price = price
    if price is None:
        return None

    percent = await prepay_percent(business)
    amount = (price * percent / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        return None

    first_session = await _first_session(session, work.id)
    client = await session.get(Client, work.client_id)
    phone = normalize_phone(client.phone) if client else None

    provider_name = (
        PaymentProvider.apipay.value
        if apipay_configured() and phone
        else PaymentProvider.manual.value
    )
    payment = Payment(
        business_id=work.business_id,
        work_id=work.id,
        session_id=first_session.id if first_session else None,
        amount=amount,
        kind=PaymentKind.prepay.value,
        status=PaymentStatus.pending.value,
        provider=provider_name,
        created_by_user_id=user.id,
        master_id=work.master_id,
        expires_at=datetime.now(UTC) + PREPAY_TTL,
        note=f"Автосчёт на предоплату {percent.normalize()}%",
    )
    session.add(payment)
    # Счёт записан до обращения к провайдеру: дальше либо успех, либо manual
    await session.commit()

    if provider_name == PaymentProvider.apipay.value:
        try:
            created = await get_provider(provider_name).create_invoice(
                payment,
                InvoiceContext(
                    client_phone=phone,
                    client_name=client.full_name if client else "",
                    description=f"Предоплата, тату-работа #{work.id}"[:60],
                ),
            )
        except PaymentProviderError as exc:
            # ApiPay недоступен — бронь подтверждена, платёж вручную
            logger.warning("Apipay prepay failed for work %s: %s — fallback manual", work.id, exc)
            payment.provider = PaymentProvider.manual.value
            payment.external_invoice_id = None
            payment.expires_at = datetime.now(UTC) + PREPAY_TTL
            await session.commit()
        else:
            payment.external_invoice_id = created.external_id
            payment.payment_url = created.payment_url
            payment.expires_at = created.expires_at or payment.expires_at
            await session.commit()
    return payment


async def _notify(telegram_id: int | None, text: str) -> None:
    if not telegram_id:
        return
    try:
        await send_text_to_telegram(telegram_id, text)
    except Exception:
        logger.warning("Failed to send Telegram notification to %s", telegram_id)


async def expire_unpaid_prepay(session) -> int:
    """Неоплаченные apipay-предоплаты старше дедлайна.

    Счёт → expired, бронь отменяется (сеансы → cancelled — слот свободен,
    события сеансов удаляются из Google Calendar), клиент и владелец
    уведомлены. Только apipay: manual-платёж мастер подтверждает сам,
    за него машина не отвечает.

    Строки счетов блокируются (FOR UPDATE) и статус перепроверяется —
    между выборкой и отметкой счёт могли оплатить (webhook). Уведомления
    и чистка календаря — ПОСЛЕ коммита: сетевые сбои не откатывают отмену
    брони и не плодят дубли при ретрае celery.
    """
    stale = (
        await session.execute(
            for_update(
                select(Payment).where(
                    Payment.status == PaymentStatus.pending.value,
                    Payment.kind == PaymentKind.prepay.value,
                    Payment.provider == PaymentProvider.apipay.value,
                    Payment.expires_at.isnot(None),
                    Payment.expires_at < datetime.now(UTC),
                ),
                session,
            )
        )
    ).scalars().all()

    notifications: list[tuple[int | None, str]] = []
    cancelled_events: list[tuple[TattooSession, Master | None, Business]] = []
    owner_tg_id: int | None = None
    for payment in stale:
        if payment.status != PaymentStatus.pending.value:
            continue  # счёт оплатили после блокировки (webhook) — не трогаем
        payment.status = PaymentStatus.expired.value
        work = await session.get(TattooWork, payment.work_id) if payment.work_id else None
        if work is None:
            continue
        client = await session.get(Client, work.client_id)
        if work.status == TattooWorkStatus.in_progress.value:
            work.status = TattooWorkStatus.cancelled.value
            # Слот освобождается: живые сеансы отменяются, их события — из календаря
            sessions = (
                await session.execute(
                    select(TattooSession).where(
                        TattooSession.work_id == work.id,
                        TattooSession.status != TattooProjectStatus.cancelled.value,
                    )
                )
            ).scalars().all()
            if sessions:
                master = await session.get(Master, work.master_id) if work.master_id else None
                business = await session.get(Business, work.business_id)
                for ts in sessions:
                    ts.status = TattooProjectStatus.cancelled.value
                    if business is not None and ts.google_event_id:
                        cancelled_events.append((ts, master, business))
        client_name = client.full_name if client else "?"
        notifications.append(
            (
                client.telegram_id if client else None,
                f"Счёт на предоплату {float(payment.amount):,.0f} ₸ не оплачен за 24 часа — "
                f"запись на тату отменена. Хочешь другое время — напиши.",
            )
        )
        if owner_tg_id is None:
            owner_tg_id = await resolve_owner_telegram_id(session)
        notifications.append(
            (
                owner_tg_id,
                f"Предоплата не пришла за 24ч — запись отменена: "
                f"{client_name}, счёт {float(payment.amount):,.0f} ₸ (работа #{work.id}).",
            )
        )
    if stale:
        await session.commit()
    for telegram_id, text in notifications:
        await _notify(telegram_id, text)
    removed_any = False
    for ts, master, business in cancelled_events:
        if await remove_session_event(ts, master, business):
            ts.google_event_id = None
            removed_any = True
    if removed_any:
        await session.commit()
    return len(stale)


def run_expire_unpaid_prepay() -> int:
    """Синхронная обёртка для celery-воркера."""

    async def _run() -> int:
        from tg_studio.db.session import async_session_factory

        async with async_session_factory() as db_session:
            return await expire_unpaid_prepay(db_session)

    return asyncio.run(_run())