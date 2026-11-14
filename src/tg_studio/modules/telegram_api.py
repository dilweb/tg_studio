"""Вебхук Telegram: нажатия inline-кнопок в напоминаниях.

Сейчас одна кнопка — «Подтвердить» присутствие в напоминании «скоро бронь»
(callback_data "presence:<booking_id>", modules/coworking/reminders.py).
Подлинность — секрет X-Telegram-Bot-Api-Secret-Token, заданный при
регистрации вебхука (scripts/set_telegram_webhook.py). Ответ всегда 200 на
обработанный/неизвестный апдейт — иначе Telegram будет слать его повторно.
"""

import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

from tg_studio.api.deps import SessionDep
from tg_studio.config import settings
from tg_studio.modules import notify
from tg_studio.modules.coworking import service as cw
from tg_studio.modules.coworking.reminders import PRESENCE_CALLBACK_PREFIX

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook", include_in_schema=False)
async def telegram_webhook(request: Request, session: SessionDep):
    secret = settings.telegram_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Вебхук Telegram не настроен")
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=403, detail="Секрет не совпал")
    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Невалидный JSON") from None

    callback = update.get("callback_query") if isinstance(update, dict) else None
    if not isinstance(callback, dict):
        return {"ok": True, "ignored": "not_callback"}
    prefix, _, raw_id = str(callback.get("data") or "").partition(":")
    if prefix != PRESENCE_CALLBACK_PREFIX or not raw_id.isdigit():
        logger.warning("Telegram callback with unknown data %r", callback.get("data"))
        return {"ok": True, "ignored": "unknown_callback"}
    from_id = (callback.get("from") or {}).get("id")
    if not isinstance(from_id, int):
        return {"ok": True, "ignored": "no_sender"}

    ok, answer = await cw.confirm_presence(session, int(raw_id), from_id)
    if ok:
        await session.commit()
    else:
        await session.rollback()

    # Ответ в Telegram — после commit; его сбой не отменяет отметку и не вызывает повтор апдейта
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    replace = None
    if ok and chat_id is not None and message.get("message_id") is not None:
        replace = (chat_id, message["message_id"], f"{message.get('text') or ''}\n\n✓ Присутствие подтверждено".strip())
    try:
        await notify.answer_callback(str(callback.get("id") or ""), answer, replace_message=replace)
    except notify.TelegramSendError as exc:
        logger.warning("Telegram answer for booking %s failed: %s", raw_id, exc)
    return {"ok": True, "confirmed": ok}
