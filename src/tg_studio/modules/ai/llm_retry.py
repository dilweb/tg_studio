"""Ретраи вызовов LLM: transient-сбои (таймаут, сеть, 5xx, rate limit)
не должны ронять ход агента — повторяем с нарастающей задержкой.

Неповторяемые ошибки (4xx — невалидный запрос, контекст переполнен и т.п.)
летят сразу: ретрай их не исправит.
"""

import asyncio
import logging

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

RETRYABLE_LLM_ERRORS = (
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
    RateLimitError,
)

LLM_CALL_MAX_ATTEMPTS = 3
LLM_CALL_BACKOFF_SECONDS = 1.5


async def chat_completion_with_retry(client, *, max_attempts: int = LLM_CALL_MAX_ATTEMPTS, **kwargs):
    """client.chat.completions.create с ретраями transient-ошибок.

    Пробрасывает любые kwargs в create (model, messages, tools, …).
    Последняя попытка наружу исходным исключением.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await client.chat.completions.create(**kwargs)
        except RETRYABLE_LLM_ERRORS as exc:
            if attempt == max_attempts:
                raise
            delay = LLM_CALL_BACKOFF_SECONDS * attempt
            logger.warning(
                "LLM: вызов не удался (попытка %d/%d): %s — повтор через %.1fс",
                attempt,
                max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
