"""API платежей (миниапп: владелец и мастера).

Обе роли выставляют счёты и подтверждают ручные оплаты: счёт клиенту
выставляет мастер (он договаривается с клиентом), владелец видит всё.
Владелец дополнительно может отменить любой счёт.

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
from sqlalchemy import select

from tg_studio.api.admin_deps import MasterBusinessAndSelfDep
from tg_studio.api.auth import CurrentUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.config import settings
from tg_studio.db.models import (
    Client,
    Master,
    Payment,
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
    TattooSession,
    TattooWork,
)
from tg_studio.modules.payments import service
from tg_studio.modules.payments.providers import (
    InvoiceContext,
    PaymentProviderError,
    apipay_configured,
    apply_provider_status,
    get_provider,
)
from tg_studio.modules.payments.schemas import (
    PaymentCancelRequest,
    PaymentCreate,
    PaymentListResponse,
    PaymentOut,
    WorkBalanceOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _fmt_money(value: Decimal | float) -> str:
    return f"{float(value):,.0f}".replace(",", " ")


async def _get_work(
    session, business, work_id: int, self_master
) -> TattooWork:
    result = await session.execute(
        select(TattooWork).where(
            TattooWork.id == work_id, TattooWork.business_id == business.id
        )
    )
    work = result.scalar_one_or_none()
    if work is None:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    if self_master is not None and work.master_id != self_master.id:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    return work


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


async def _reread_payment(session, business, payment_id: int) -> PaymentOut:
    """Свежая строка с именами (SQLite не возвращает server_default)."""
    stmt = (
        select(Payment, Master.full_name, Client.full_name)
        .join(TattooWork, Payment.work_id == TattooWork.id, isouter=True)
        .join(Client, TattooWork.client_id == Client.id, isouter=True)
        .outerjoin(Master, Payment.master_id == Master.id)
        .where(Payment.id == payment_id)
    )
    payment, master_name, client_name = (await session.execute(stmt)).one()
    return PaymentOut(
        id=payment.id,
        work_id=payment.work_id,
        session_id=payment.session_id,
        amount=float(payment.amount),
        kind=payment.kind,
        status=payment.status,
        provider=payment.provider,
        external_invoice_id=payment.external_invoice_id,
        payment_url=payment.payment_url,
        expires_at=payment.expires_at,
        master_id=payment.master_id,
        master_name=master_name,
        client_name=client_name,
        note=payment.note,
        created_at=payment.created_at,
        paid_at=payment.paid_at,
    )


def _normalize_phone(raw: str | None) -> str | None:
    return service.normalize_phone(raw)


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    work_id: int | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Платежи бизнеса. Мастер — только по своим работам, владелец — все."""
    business, self_master = actor
    stmt = (
        select(Payment, Master.full_name, Client.full_name)
        .join(TattooWork, Payment.work_id == TattooWork.id)
        .join(Client, TattooWork.client_id == Client.id)
        .outerjoin(Master, Payment.master_id == Master.id)
        .where(Payment.business_id == business.id)
        .order_by(Payment.id.desc())
        .limit(limit)
    )
    if self_master is not None:
        stmt = stmt.where(TattooWork.master_id == self_master.id)
    if work_id is not None:
        stmt = stmt.where(Payment.work_id == work_id)
    if status is not None:
        stmt = stmt.where(Payment.status == status)
    rows = (await session.execute(stmt)).all()
    payments = [
        PaymentOut(
            id=p.id,
            work_id=p.work_id,
            session_id=p.session_id,
            amount=float(p.amount),
            kind=p.kind,
            status=p.status,
            provider=p.provider,
            external_invoice_id=p.external_invoice_id,
            payment_url=p.payment_url,
            expires_at=p.expires_at,
            master_id=p.master_id,
            master_name=master_name,
            client_name=client_name,
            note=p.note,
            created_at=p.created_at,
            paid_at=p.paid_at,
        )
        for p, master_name, client_name in rows
    ]
    return PaymentListResponse(payments=payments, total=len(payments))


@router.get("/works/{work_id}/balance", response_model=WorkBalanceOut)
async def work_balance(work_id: int, session: SessionDep, actor: MasterBusinessAndSelfDep):
    """Баланс работы для диалога завершения сеанса: цена, оплачено, остаток."""
    business, self_master = actor
    work = await _get_work(session, business, work_id, self_master)
    client = await session.get(Client, work.client_id)
    balance, paid = await service.work_balance(session, work)
    return WorkBalanceOut(
        work_id=work.id,
        client_id=work.client_id,
        client_name=client.full_name if client else "",
        client_phone=_normalize_phone(client.phone) if client else None,
        contract_price=float(work.contract_price) if work.contract_price is not None else None,
        paid_total=float(paid),
        balance=float(balance) if balance is not None else None,
        apipay_ready=apipay_configured(),
    )


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

    payment = (
        await session.execute(
            select(Payment).where(
                Payment.external_invoice_id == external_id,
                Payment.provider == PaymentProvider.apipay.value,
            )
        )
    ).scalar_one_or_none()
    if payment is None:
        logger.warning("Apipay webhook for unknown invoice %s", external_id)
        return {"ok": True, "unknown": external_id}

    paid_at = _parse_iso(invoice.get("paid_at"))
    if paid_at is None and provider_status == "paid":
        paid_at = datetime.now(UTC)
    changed = apply_provider_status(payment, provider_status, paid_at)
    if changed:
        invoice_amount = invoice.get("amount")
        if invoice_amount is not None:
            try:
                if Decimal(str(invoice_amount)) != payment.amount:
                    logger.warning(
                        "Apipay amount mismatch for payment %s: invoice %s vs %s",
                        payment.id, invoice_amount, payment.amount,
                    )
            except Exception:
                pass
        await session.commit()
    return {"ok": True, "changed": changed}


# ---------------------------------------------------------------------------
# Счета
# ---------------------------------------------------------------------------


@router.post("", response_model=PaymentOut, status_code=201)
async def create_payment(
    body: PaymentCreate,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Выставить счёт по работе.

    contract_price: None — не трогать; при первом счёте работы фиксирует
    договорную цену (дальше менять только явным пересмотром).
    kind: None → final, если сумма закрывает баланс, иначе partial.
    """
    business, self_master = actor
    work = await _get_work(session, business, body.work_id, self_master)

    session_id = body.session_id
    if session_id is not None:
        sess = (
            await session.execute(
                select(TattooSession).where(
                    TattooSession.id == session_id, TattooSession.work_id == work.id
                )
            )
        ).scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="Сеанс не найден в этой работе")

    # Фиксация/пересмотр договорной цены — до расчёта баланса и kind
    if body.contract_price is not None and (
        work.contract_price is None or _dec(body.contract_price) != _dec(work.contract_price)
    ):
        work.contract_price = _dec(body.contract_price)

    provider_name = body.provider or ("apipay" if apipay_configured() else "manual")
    if provider_name == PaymentProvider.apipay.value and not apipay_configured():
        raise HTTPException(status_code=409, detail="ApPay не подключён")
    if body.paid_now and provider_name != PaymentProvider.manual.value:
        raise HTTPException(
            status_code=422,
            detail="«Оплатил» доступен только для ручных платежей (наличные/перевод)",
        )

    balance, _paid = await service.work_balance(session, work)
    amount = _dec(body.amount)
    if body.kind is not None:
        kind = body.kind.value
    else:
        kind = (
            PaymentKind.final.value
            if balance is not None and amount >= balance
            else PaymentKind.partial.value
        )

    client = await session.get(Client, work.client_id)
    payment = Payment(
        business_id=business.id,
        work_id=work.id,
        session_id=session_id,
        amount=amount,
        kind=kind,
        status=PaymentStatus.pending.value,
        provider=provider_name,
        created_by_user_id=user.id,
        master_id=self_master.id if self_master is not None else None,
        note=body.note,
    )
    session.add(payment)
    await session.flush()

    if provider_name == PaymentProvider.apipay.value:
        try:
            created = await get_provider(provider_name).create_invoice(
                payment,
                InvoiceContext(
                    client_phone=_normalize_phone(client.phone) if client else None,
                    client_name=client.full_name if client else "",
                    description=f"Тату-работа #{work.id}",
                ),
            )
        except PaymentProviderError as exc:
            await session.rollback()
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        payment.external_invoice_id = created.external_id
        payment.payment_url = created.payment_url
        payment.expires_at = created.expires_at

    if body.paid_now:
        payment.status = PaymentStatus.paid.value
        payment.paid_at = datetime.now(UTC)
        payment.confirmed_by_user_id = user.id

    await session.commit()
    return await _reread_payment(session, business, payment.id)


@router.post("/{payment_id}/mark-paid", response_model=PaymentOut)
async def mark_paid(
    payment_id: int,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Подтвердить ручную оплату: клиент отдал деньги / перевёл на карту.

    Для apipay-платежей недоступно — их оплачивает вебхук платёжной системы.
    """
    business, _self_master = actor
    payment = await _get_payment(session, business, payment_id)
    if payment.status != PaymentStatus.pending.value:
        raise HTTPException(status_code=409, detail="Счёт уже не ожидает оплаты")
    if payment.provider != PaymentProvider.manual.value:
        raise HTTPException(
            status_code=409, detail="Оплату этого счёта подтверждает платёжная система"
        )
    payment.status = PaymentStatus.paid.value
    payment.paid_at = datetime.now(UTC)
    payment.confirmed_by_user_id = user.id
    await session.commit()
    return await _reread_payment(session, business, payment.id)


@router.post("/{payment_id}/cancel", response_model=PaymentOut)
async def cancel_payment(
    payment_id: int,
    body: PaymentCancelRequest,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
):
    """Отменить счёт (pending). Причина — в note."""
    business, _self_master = actor
    payment = await _get_payment(session, business, payment_id)
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
):
    """Опросить ApiPay и подтянуть актуальный статус счёта.

    Страховка вебхука (быстрый старт до подключения уведомлений) и сверка.
    «paid» липкий: повторный опрос уже оплаченного счёта ничего не испортит;
    из cancelled/expired в paid — разрешено (ApiPay так умеет).
    """
    business, _self_master = actor
    payment = await _get_payment(session, business, payment_id)
    if payment.provider != PaymentProvider.apipay.value:
        raise HTTPException(status_code=409, detail="Опрос статуса — только для счетов Kaspi")
    if not payment.external_invoice_id:
        raise HTTPException(status_code=409, detail="У платежа нет внешнего счёта")
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
    await session.commit()
    return await _reread_payment(session, business, payment.id)
