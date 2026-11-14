"""Мок ApiPay (Kaspi) для нагрузочного теста — «банк», который знает правду о деньгах.

API как у настоящего ApiPay (то, что вызывает tg_studio.modules.payments.providers):
  POST /invoices               — создать счёт (идемпотентно по external_order_id_idempotency)
  GET  /invoices/{id}          — статус счёта
  POST /invoices/{id}/cancel   — отменить (оплаченный отменить нельзя — 409)

Вебхук invoice.status_changed подписывается HMAC-SHA256 тем же секретом, что у сервиса
(APIPAY_WEBHOOK_SECRET), и шлётся на WEBHOOK_URL.

Управление (только для скрипта нагрузки), /_control/*:
  PUT  /_control/behaviour/{phone}  {create: ok|error|pay_on_create, create_delay: сек, cancel: ok|error}
  POST /_control/pay      {order, webhooks=1, deliver=true, amount_delta=0} — «клиент оплатил»
  POST /_control/webhook  {order, times=1}  — повторить вебхук с текущим статусом
  GET  /_control/invoices — все счета (выгрузка для сверки)

Банк сериализует операции над счётом: оплата и отмена одного счёта не пересекаются —
кто первый, тот и решил (оплаченный не отменить, отменённый не оплатить).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from tg_studio.config import settings

logger = logging.getLogger("apipay_mock")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "http://api:8000/api/payments/webhook/apipay")

app = FastAPI(title="ApiPay mock")

_lock = asyncio.Lock()
_invoices: dict[str, dict] = {}  # id → счёт
_by_idempotency: dict[str, str] = {}  # ключ идемпотентности → id
_behaviour: dict[str, dict] = {}  # телефон → поведение
_seq = 0


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _public(inv: dict) -> dict:
    return {k: v for k, v in inv.items() if not k.startswith("_")}


def _host_header() -> dict:
    """Сервис за TrustedHostMiddleware принимает только публичный Host."""
    if not settings.trusted_hosts:
        return {}
    netloc = urlsplit(settings.api_public_url or settings.public_url).netloc
    return {"Host": netloc} if netloc else {}


async def _send_webhook(inv: dict, amount_delta: int = 0) -> dict:
    body = json.dumps(
        {
            "event": "invoice.status_changed",
            "invoice": {
                "id": inv["id"],
                "status": inv["status"],
                "amount": inv["amount"] + amount_delta,
                "paid_at": inv["paid_at"],
                "external_order_id": inv["external_order_id"],
            },
        }
    ).encode()
    sig = "sha256=" + hmac.new(settings.apipay_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-Webhook-Signature": sig, **_host_header()}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(WEBHOOK_URL, content=body, headers=headers)
        result = {"status": resp.status_code, "body": resp.text[:500]}
    except httpx.HTTPError as exc:
        result = {"status": None, "error": f"{type(exc).__name__}: {exc}"}
    inv["_webhooks"].append({"at": _now(), "status": inv["status"], "amount_delta": amount_delta, **result})
    return result


def _check_key(request: Request) -> None:
    if request.headers.get("X-API-Key") != settings.apipay_api_key:
        raise HTTPException(status_code=401, detail="bad api key")


# ---------------------------------------------------------------------------
# API ApiPay
# ---------------------------------------------------------------------------


@app.post("/invoices", status_code=201)
async def create_invoice(request: Request):
    global _seq
    _check_key(request)
    data = await request.json()
    phone = data.get("phone_number") or ""
    beh = _behaviour.get(phone, {})
    if beh.get("create_delay"):
        await asyncio.sleep(float(beh["create_delay"]))
    if beh.get("create") == "error":
        return JSONResponse(status_code=500, content={"error_code": "mock_internal_error"})
    key = data.get("external_order_id_idempotency") or ""
    async with _lock:
        existing = _by_idempotency.get(key) if key else None
        if existing:
            inv = _invoices[existing]
        else:
            _seq += 1
            inv_id = f"mock-{_seq}"
            inv = {
                "id": inv_id,
                "external_order_id": data.get("external_order_id"),
                "phone_number": phone,
                "amount": int(data.get("amount") or 0),
                "description": data.get("description"),
                "status": "pending",
                "kaspi_qr_link": f"https://mock.apipay/pay/{inv_id}",
                "created_at": _now(),
                "paid_at": None,
                "cancelled_at": None,
                "_webhooks": [],
                "_cancel_calls": 0,
            }
            _invoices[inv_id] = inv
            if key:
                _by_idempotency[key] = inv_id
        if beh.get("create") == "pay_on_create" and inv["status"] == "pending":
            inv["status"] = "paid"
            inv["paid_at"] = _now()
    if beh.get("create") == "pay_on_create":
        # вебхук «оплачен» приходит раньше, чем сервис получил ответ с id счёта
        await _send_webhook(inv)
    return _public(inv)


@app.get("/invoices/{inv_id}")
async def get_invoice(inv_id: str, request: Request):
    _check_key(request)
    inv = _invoices.get(inv_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="not found")
    return _public(inv)


@app.post("/invoices/{inv_id}/cancel")
async def cancel_invoice(inv_id: str, request: Request):
    _check_key(request)
    async with _lock:
        inv = _invoices.get(inv_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="not found")
        inv["_cancel_calls"] += 1
        if _behaviour.get(inv["phone_number"], {}).get("cancel") == "error":
            return JSONResponse(status_code=500, content={"error_code": "mock_internal_error"})
        if inv["status"] == "paid":
            return JSONResponse(status_code=409, content={"error_code": "invoice_already_paid"})
        if inv["status"] == "pending":
            inv["status"] = "cancelled"
            inv["cancelled_at"] = _now()
        return _public(inv)


# ---------------------------------------------------------------------------
# Управление для скрипта нагрузки
# ---------------------------------------------------------------------------


def _find_order(order: str) -> dict:
    """Счёт по external_order_id. Несколько счетов на один заказ — берём последний
    (дубль сам по себе находка сверки, её покажет выгрузка /_control/invoices)."""
    found = [inv for inv in _invoices.values() if inv["external_order_id"] == order]
    if not found:
        raise HTTPException(status_code=404, detail=f"no invoice for {order}")
    return found[-1]


@app.put("/_control/behaviour/{phone}")
async def set_behaviour(phone: str, request: Request):
    _behaviour[phone] = await request.json()
    return {"ok": True}


@app.post("/_control/pay")
async def control_pay(request: Request):
    data = await request.json()
    async with _lock:
        inv = _find_order(data["order"])
        if inv["status"] != "pending":
            return {"paid": False, "reason": inv["status"], "invoice": _public(inv)}
        inv["status"] = "paid"
        inv["paid_at"] = _now()
    results = []
    if data.get("deliver", True):
        delta = int(data.get("amount_delta", 0))
        results = await asyncio.gather(
            *(_send_webhook(inv, delta) for _ in range(int(data.get("webhooks", 1))))
        )
    return {"paid": True, "invoice": _public(inv), "webhooks": results}


@app.post("/_control/webhook")
async def control_webhook(request: Request):
    data = await request.json()
    inv = _find_order(data["order"])
    results = await asyncio.gather(*(_send_webhook(inv) for _ in range(int(data.get("times", 1)))))
    return {"invoice": _public(inv), "webhooks": results}


@app.get("/_control/invoices")
async def control_invoices():
    return [
        {**_public(inv), "webhooks": inv["_webhooks"], "cancel_calls": inv["_cancel_calls"]}
        for inv in _invoices.values()
    ]
