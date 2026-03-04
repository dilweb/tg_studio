"""
Валидация Telegram Mini App initData.

Документация: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Флоу:
1. Клиент (Mini App) передаёт initData в заголовке Authorization: TelegramInitData <value>
2. Бэкенд проверяет HMAC-SHA256 подпись — только Telegram мог её создать
3. Из проверенного initData извлекается telegram_id пользователя
"""

import hashlib
import hmac
import json
import time
from typing import Annotated
from urllib.parse import parse_qsl, unquote

from fastapi import Depends, Header, HTTPException

from tg_studio.config import settings

# initData действительна 24 часа (можно уменьшить для большей безопасности)
INIT_DATA_MAX_AGE_SECONDS = 86400


def _validate_init_data(init_data: str) -> dict:
    """
    Проверить подпись initData и вернуть данные пользователя.
    Выбрасывает HTTPException 401 если подпись невалидна.
    """
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing hash in initData")

    # Строка для проверки: все поля кроме hash, отсортированные по ключу
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )

    # Секретный ключ = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(
        b"WebAppData",
        settings.bot_token.encode(),
        hashlib.sha256,
    ).digest()

    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid initData signature")

    # Проверить срок действия
    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="initData expired")

    # Распаковать объект user
    user_raw = parsed.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="No user in initData")

    return json.loads(unquote(user_raw))


def get_telegram_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """
    FastAPI dependency — извлечь и проверить пользователя из заголовка.

    Использование в роутере:
        @router.post("/something")
        async def handler(tg_user: TelegramUserDep):
            telegram_id = tg_user["id"]
    """
    if settings.debug and authorization is None:
        # В режиме разработки можно передать заглушку через X-Debug-User
        # В продакшне это поле игнорируется
        return {"id": 0, "first_name": "Dev", "last_name": "User"}

    if not authorization or not authorization.startswith("TelegramInitData "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header required: 'TelegramInitData <initData>'",
        )

    init_data = authorization.removeprefix("TelegramInitData ")
    return _validate_init_data(init_data)


TelegramUserDep = Annotated[dict, Depends(get_telegram_user)]
