"""API платежей (миниапп): счета за брони мест (04) — чтение, ручное подтверждение,
отмена, опрос статуса, вебхук. Счета создаёт коворкинг (POST /bookings/{id}/pay).

Машина состояний: pending → paid | cancelled | expired. Провайдер manual —
кнопка «Оплатил»; apipay (Kaspi) — вебхук с HMAC-подписью, ручное
подтверждение запрещено; опрос GET /invoices/{id} (POST /{id}/sync) —
страховка, если вебхук не дошёл.
"""

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from tg_studio.api.admin_deps import AdminBusinessAndSelfDep, MasterBusinessAndSelfDep
from tg_studio.api.auth import CurrentUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.config import settings
from tg_studio.db.locks import for_update
from tg_studio.db.models import (
    Booking,
    Master,
    Payment,
    PaymentProvider,
    PaymentStatus,
    User,
    UserRole,
)
from tg_studio.modules.payments.providers import (
    PaymentProviderError,
    apply_provider_status,
    get_provider,
)
from tg_studio.modules.payments.schemas import (
    PaymentCancelRequest,
    PaymentListResponse,
    PaymentOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


async def _get_payment(session, business, payment_id: int) -> Payment:
    result = await session.execute(
        select(Payment).where(
            Payment.id == payment_id, Payment.business_id == business.id
        )
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Платёж не найден")
    return payment


async def _lock_payment(session, payment_id: int) -> Payment:
    """Свежая строка счёта под FOR UPDATE (вебхук, sync и ручная отметка не гонятся)."""
    return (
        await session.execute(
            for_update(
                select(Payment).where(Payment.id == payment_id).execution_options(populate_existing=True),
                session,
            )
        )
    ).scalar_one()


async def _get_payment_for_actor(
    session, business, payment_id: int, actor_user: User, self_master: Master | None
) -> Payment:
    """Платёж актора: мастер действует только по счетам за СВОИ брони (04)
    (иначе мог бы отметить чужую оплату или отменить чужой счёт).
    Владелец и администратор студии (05) — все счета бизнеса."""
    payment = await _get_payment(session, business, payment_id)
    if actor_user.role == UserRole.master and self_master is not None:
        booking = await session.get(Booking, payment.booking_id) if payment.booking_id else None
        if booking is None or booking.master_id != self_master.id:
            raise HTTPException(status_code=404, detail="Платёж не найден")
    return payment


async def _reread_payment(session, business, payment_id: int) -> PaymentOut:
    """Свежая строка с именем мастера (SQLite не возвращает server_default)."""
    stmt = (
        select(Payment, Master.full_name)
        .outerjoin(Master, Payment.master_id == Master.id)
        .where(Payment.id == payment_id)
    )
    payment, master_name = (await session.execute(stmt)).one()
    return _payment_out(payment, master_name)


def _payment_out(p: Payment, master_name: str | None) -> PaymentOut:
    return PaymentOut(
        id=p.id,
        booking_id=p.booking_id,
        amount=float(p.amount),
        kind=p.kind,
        status=p.status,
        provider=p.provider,
        external_invoice_id=p.external_invoice_id,
        payment_url=p.payment_url,
        expires_at=p.expires_at,
        master_id=p.master_id,
        master_name=master_name,
        note=p.note,
        created_at=p.created_at,
        paid_at=p.paid_at,
    )


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    booking_id: int | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Платежи бизнеса. Мастер — только свои, владелец/админ — все (05)."""
    business, self_master = actor
    stmt = (
        select(Payment, Master.full_name)
        .outerjoin(Master, Payment.master_id == Master.id)
        .where(Payment.business_id == business.id)
    )
    if self_master is not None:
        stmt = stmt.where(Payment.master_id == self_master.id)
    if booking_id is not None:
        stmt = stmt.where(Payment.booking_id == booking_id)
    if status is not None:
        stmt = stmt.where(Payment.status == status)
    total = (
        await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    rows = (await session.execute(stmt.order_by(Payment.id.desc()).limit(limit).offset(offset))).all()
    payments = [_payment_out(p, master_name) for p, master_name in rows]
    return PaymentListResponse(payments=payments, total=total)


# ---------------------------------------------------------------------------
# Вебхук ApiPay (публичный, без авторизации — подлинность = подпись)
# ---------------------------------------------------------------------------


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@router.post("/webhook/apipay", include_in_schema=False)
async def apipay_webhook(request: Request, session: SessionDep):
    """Уведомление ApiPay об оплате (event=invoice.status_changed).

    Подпись X-Webhook-Signature: sha256=<hex> — HMAC-SHA256 от СЫРОГО тела
    (до парсинга), сравнение за константное время. Отвечаем 2xx быстро:
    не дергаем внешних API. Неизвестный счёт не 404 (не провоцируем ретраи) —
    лог и ok; повтор по (invoice.id, status) безвреден: применение статуса
    идемпотентно, «paid» липкий (cancelled→paid разрешён, paid→cancelled нет).
    """
    secret = settings.apipay_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Вебхук не настроен")
    raw = await request.body()
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    provided = request.headers.get("X-Webhook-Signature", "")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="Подпись не совпала")

    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Невалидный JSON") from None

    event = payload.get("event")
    if event != "invoice.status_changed":
        logger.info("Ignoring apipay webhook event %r", event)
        return {"ok": True, "ignored": event}

    invoice = payload.get("invoice") or {}
    external_id = str(invoice.get("id") or "")
    provider_status = invoice.get("status") or ""
    if not external_id or not provider_status:
        raise HTTPException(status_code=400, detail="Нет invoice.id или status")

    # FOR UPDATE: вебхук и celery-sweep (expire_unpaid_prepay) не должны
    # одновременно решать судьбу одного счёта — кто первый залочил, тот и
    # видит актуальный статус; второй после блокировки перепроверяет
    payment = (
        await session.execute(
            for_update(
                select(Payment).where(
                    Payment.external_invoice_id == external_id,
                    Payment.provider == PaymentProvider.apipay.value,
                ),
                session,
            )
        )
    ).scalar_one_or_none()
    if payment is None:
        logger.warning("Apipay webhook for unknown invoice %s", external_id)
        return {"ok": True, "unknown": external_id}

    # Сверка суммы: если вебхук сообщает amount и он не совпадает с нашей
    # записью — это либо чужой счёт с перехваченным id, либо баг интеграции.
    # Статус «paid» при расхождении НЕ применяем: деньги считаем по нашей сумме.
    invoice_amount = invoice.get("amount")
    amount_mismatch = False
    if invoice_amount is not None:
        try:
            amount_mismatch = Decimal(str(invoice_amount)) != payment.amount
        except Exception:
            amount_mismatch = True
    if amount_mismatch:
        logger.warning(
            "Apipay amount mismatch for payment %s (invoice %s): webhook %s vs db %s — "
            "status not applied, manual review required",
            payment.id,
            external_id,
            invoice_amount,
            payment.amount,
        )
        return {"ok": True, "ignored": "amount_mismatch", "payment_id": payment.id}

    paid_at = _parse_iso(invoice.get("paid_at"))
    if paid_at is None and provider_status == "paid":
        paid_at = datetime.now(UTC)
    changed = apply_provider_status(payment, provider_status, paid_at)
    if changed and payment.status == PaymentStatus.paid.value:
        # Коворкинг (04): счёт оплачен → проводка topup (+ подтверждение брони, если счёт за бронь;
        # дедуп: повторный вебхук — no-op в apply_payment_paid)
        from tg_studio.modules.coworking import money

        await money.apply_payment_paid(session, payment, paid_at=paid_at)
    if changed:
        await session.commit()
    return {"ok": True, "changed": changed}


# ---------------------------------------------------------------------------
# Счета
# ---------------------------------------------------------------------------



@router.post("/{payment_id}/mark-paid", response_model=PaymentOut)
async def mark_paid(
    payment_id: int,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Подтвердить ручную оплату: деньги принял владелец или администратор студии (04, 05).

    Мастер подтвердить свою оплату не может — иначе бронь подтверждалась бы без денег.
    Для apipay-платежей недоступно — их оплачивает вебхук платёжной системы.
    """
    business, _ = actor
    await _get_payment(session, business, payment_id)
    # FOR UPDATE: повторное нажатие/параллельная отметка не должны задвоить topup
    payment = await _lock_payment(session, payment_id)
    if payment.status != PaymentStatus.pending.value:
        raise HTTPException(status_code=409, detail="Счёт уже не ожидает оплаты")
    if payment.provider != PaymentProvider.manual.value:
        raise HTTPException(
            status_code=409, detail="Оплату этого счёта подтверждает платёжная система"
        )
    from tg_studio.modules.coworking import money

    # топап баланса мастера (+ подтверждение брони, если счёт за бронь)
    await money.apply_payment_paid(session, payment, actor_user_id=user.id)
    await session.commit()
    return await _reread_payment(session, business, payment.id)


@router.post("/{payment_id}/cancel", response_model=PaymentOut)
async def cancel_payment(
    payment_id: int,
    body: PaymentCancelRequest,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Отменить счёт (pending). Причина — в note."""
    business, self_master = actor
    payment = await _get_payment_for_actor(session, business, payment_id, user, self_master)
    if payment.status != PaymentStatus.pending.value:
        raise HTTPException(status_code=409, detail="Счёт уже не ожидает оплаты")
    if payment.provider == PaymentProvider.apipay.value:
        try:
            await get_provider(payment.provider).cancel_invoice(payment)
        except PaymentProviderError as exc:
            logger.warning("Failed to cancel apipay invoice %s: %s", payment.id, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    payment.status = PaymentStatus.cancelled.value
    if body.note:
        payment.note = body.note
    await session.commit()
    return await _reread_payment(session, business, payment.id)


@router.post("/{payment_id}/sync", response_model=PaymentOut)
async def sync_payment(
    payment_id: int,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Опросить ApiPay и подтянуть актуальный статус счёта.

    Страховка вебхука (быстрый старт до подключения уведомлений) и сверка.
    «paid» липкий: повторный опрос уже оплаченного счёта ничего не испортит;
    из cancelled/expired в paid — разрешено (ApiPay так умеет).
    """
    business, self_master = actor
    payment = await _get_payment_for_actor(session, business, payment_id, user, self_master)
    if payment.provider != PaymentProvider.apipay.value:
        raise HTTPException(status_code=409, detail="Опрос статуса — только для счетов Kaspi")
    if not payment.external_invoice_id:
        raise HTTPException(status_code=409, detail="У платежа нет внешнего счёта")
    payment = await _lock_payment(session, payment.id)
    try:
        invoice = await get_provider(payment.provider).fetch_invoice(payment)
    except PaymentProviderError as exc:
        logger.warning("Failed to fetch apipay invoice for payment %s: %s", payment.id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    apply_provider_status(payment, invoice.get("status") or "", _parse_iso(invoice.get("paid_at")))
    link = invoice.get("kaspi_qr_link")
    if link and payment.payment_url != link:
        payment.payment_url = link
    if invoice.get("status") == "error" and invoice.get("error_message"):
        payment.note = f"ApiPay: {invoice['error_message']}"
    # Коворкинг (04): опрос подтянул «paid» по счёту за бронь → проводка + бронь
    if payment.status == PaymentStatus.paid.value:
        from tg_studio.modules.coworking import money

        await money.apply_payment_paid(session, payment)
    await session.commit()
    return await _reread_payment(session, business, payment.id)
