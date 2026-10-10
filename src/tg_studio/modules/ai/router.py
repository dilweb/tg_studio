"""Роутер клиентских сообщений: агент записи или мастер.

После эскалации чат закреплён за мастером — дальнейшие сообщения клиента
нужно отправлять мастеру, а не боту. Разбирает лёгкая LLM (Gemma через
OpenRouter) по последним 10 сообщениям с датами: продолжение диалога с
мастером → MASTER, новая заявка/вопрос про мастеров → AI.

До первой эскалации LLM не вызывается вообще — всё агенту. Любой сбой
(нет ответа, не разобрали) → MASTER: человека не теряем.
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.config import settings
from tg_studio.db.models import ChatAssignmentStatus, ChatDirection, ChatMessage
from tg_studio.modules.chat.assignments import get_open_assignment
from tg_studio.modules.chat.service import PREVIEW_LABELS

logger = logging.getLogger(__name__)

ROUTE_AGENT = "agent"
ROUTE_MASTER = "master"

HISTORY_SIZE = 10

DISPATCHER_PROMPT = """\
Ты — диспетчер переписки клиента тату-студии. Ровно недавно в диалоге был момент, \
когда AI-ассистент передал чат живому мастеру (клиент закреплён за мастером). \
Тебе показывают последние сообщения переписки с датами. Определи, кому адресовано \
НОВОЕ сообщение клиента.

Ответь одним словом:
- MASTER — если сообщение является продолжением диалога с мастером: уточнение записи, \
вопрос по своему заказу или тату (цена, уход, заживление, сроки), согласование времени, \
благодарность, продолжение начатой темы.
- AI — если клиент начинает новую тему: новая заявка на запись, вопрос про мастеров, \
стили, портфолио, просьба вернуть AI-ассистента.

Правила:
- Вопрос «сколько стоит» по только что обсуждаемой работе — это к мастеру (MASTER), \
а абстрактный вопрос о ценах студии — к AI.
- Если сомневаешься — отвечай MASTER.

Последние сообщения:
{history}

Новое сообщение клиента ({today}): {new_message}

Ответь одним словом: MASTER или AI.
"""

_DIR_LABELS = {
    ChatDirection.from_client: "Клиент",
    ChatDirection.from_master: "Мастер",
    ChatDirection.from_ai: "Ассистент",
}


def parse_verdict(text: str | None) -> str | None:
    """Одно слово в ответе модели → route; иначе None (не разобрали)."""
    if not text:
        return None
    upper = text.upper()
    if "MASTER" in upper:
        return ROUTE_MASTER
    if "AI" in upper:
        return ROUTE_AGENT
    return None


def format_history(messages: list[ChatMessage]) -> str:
    """История для диспетчера: дата, отправитель, текст (медиа — меткой)."""
    lines = []
    for msg in messages:
        label = _DIR_LABELS.get(msg.direction, "Клиент")
        content = msg.content if msg.content else PREVIEW_LABELS.get(
            msg.file_kind or "", "📎 Вложение"
        )
        if len(content) > 200:
            content = content[:197] + "…"
        ts = msg.created_at.strftime("%d.%m.%Y %H:%M") if msg.created_at else ""
        lines.append(f"[{ts}] {label}: {content}")
    return "\n".join(lines)


async def load_history(session: AsyncSession, client_id: int) -> list[ChatMessage]:
    """Последние HISTORY_SIZE сообщений клиента в хронологическом порядке."""
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.client_id == client_id)
        .order_by(ChatMessage.id.desc())
        .limit(HISTORY_SIZE)
    )
    return list(reversed(result.scalars().all()))


async def classify_message(
    session: AsyncSession, client_id: int, new_message: str
) -> str:
    """Кому адресовано сообщение: ROUTE_AGENT или ROUTE_MASTER.

    Без открытого закрепления LLM не дёргаем — всё агенту.
    """
    assignment = await get_open_assignment(session, client_id)
    if assignment is None:
        return ROUTE_AGENT

    history = await load_history(session, client_id)
    prompt = DISPATCHER_PROMPT.format(
        history=format_history(history),
        today=date.today().strftime("%d.%m.%Y"),
        new_message=new_message[:500],
    )
    try:
        from tg_studio.modules.ai.client import get_openai_client

        client = get_openai_client()
        response = await client.chat.completions.create(
            model=settings.llm_router_model or settings.llm_model,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        verdict = parse_verdict(response.choices[0].message.content)
    except Exception:
        logger.exception("Router LLM call failed — falling back to master")
        verdict = None

    if verdict is None:
        logger.warning("Router verdict unparsable — falling back to master")
        return ROUTE_MASTER

    logger.info(
        "Router: client %s message routed to %s (assignment %s)",
        client_id,
        verdict,
        assignment.id,
    )
    return verdict
