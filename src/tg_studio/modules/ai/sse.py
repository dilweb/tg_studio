"""
SSE-стриминг ответа AI-чата (как в documind: `data: {json}\\n\\n`).

Пока LLM и цикл инструментов работают, в очередь попадают события `on_event`
(ход цикла, вызов tool). Затем ответ дробится на `delta` и завершается `final`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.db.models import Business
from tg_studio.modules.ai.client import ChatResult, ConversationNotFoundError, chat
from tg_studio.modules.ai.schemas import AIChatRequest

type IsDisconnected = Callable[[], Coroutine[Any, Any, bool]] | None


def _sse_data(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chunk_text(text: str, size: int = 120) -> list[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


async def stream_ai_chat_sse(
    session: AsyncSession,
    business: Business,
    user_id: int,
    body: AIChatRequest,
    *,
    is_disconnected: IsDisconnected = None,
) -> AsyncIterator[str]:
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def on_event(ev: dict[str, Any]) -> None:
        await queue.put(("event", ev))

    async def runner() -> None:
        try:
            res = await chat(
                session=session,
                business=business,
                user_id=user_id,
                user_message=body.message.strip(),
                conversation_id=body.conversation_id,
                confirmed=body.confirmed,
                on_event=on_event,
            )
        except Exception as e:
            await queue.put(("error", e))
        else:
            await queue.put(("result", res))
        finally:
            await queue.put(("end", None))

    task = asyncio.create_task(runner())
    try:
        yield _sse_data(
            {
                "type": "start",
                "conversation_id": body.conversation_id,
            }
        )
        while True:
            if is_disconnected and await is_disconnected():
                return
            kind, data = await queue.get()
            if kind == "end":
                break
            if kind == "event":
                yield _sse_data(data)
            elif kind == "result":
                r: ChatResult = data
                for part in _chunk_text(r.reply):
                    if is_disconnected and await is_disconnected():
                        return
                    yield _sse_data({"type": "delta", "text": part})
                yield _sse_data(
                    {
                        "type": "final",
                        "response": r.reply,
                        "conversation_id": r.conversation_id,
                        "awaiting_confirmation": r.awaiting_confirmation,
                    }
                )
            elif kind == "error":
                exc = data
                if isinstance(exc, ConversationNotFoundError):
                    msg = "Диалог не найден"
                else:
                    msg = str(exc)
                yield _sse_data({"type": "error", "message": msg})
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
