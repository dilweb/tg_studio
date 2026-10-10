"""Ход агента записи в TG-боте: один входящий текст → один ответ.

Переиспользует generic-луп ai/client.chat() с booking-тулами. При
эскалации луп останавливается по флагу в registry (EscalationState):
клиенту уходит фиксированный прощальный текст, мастеру и владельцу —
уведомления. Пер-клиентский lock от параллельных ходов на быстрые
сообщения подряд.
"""

import asyncio
import logging
from functools import partial

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.db.models import Business, Client, Master
from tg_studio.modules.chat import assignments
from tg_studio.modules.chat.service import send_text_to_telegram

from .booking_tools import BOOKING_TOOLS, EscalationState, booking_tool_registry
from .client import ChatImage, chat
from .system_prompt import build_booking_agent_prompt

logger = logging.getLogger(__name__)

ESCALATION_CLIENT_REPLY = (
    "Отлично! Я передал ваш заказ мастеру — он напишет вам здесь лично "
    "в ближайшее время и всё согласует 😊"
)
FAILURE_CLIENT_REPLY = (
    "Извините, произошла техническая неполадка. Мы уже уведомили студию — "
    "ваше сообщение не потерялось, скоро вам ответят."
)

_client_locks: dict[int, asyncio.Lock] = {}


def _lock_for(client_id: int) -> asyncio.Lock:
    lock = _client_locks.get(client_id)
    if lock is None:
        lock = asyncio.Lock()
        _client_locks[client_id] = lock
    return lock


async def _master_names_for_prompt(session: AsyncSession, business_id: int) -> str:
    """Имена активных мастеров строкой — в промпт на каждый ход, чтобы агент
    копировал точные имена в escalate_to_master (см. match_masters)."""
    result = await session.execute(
        select(Master.full_name).where(
            Master.business_id == business_id, Master.is_active.is_(True)
        )
    )
    return ", ".join(result.scalars().all())


async def run_booking_turn(
    session: AsyncSession,
    business: Business,
    client: Client,
    text: str,
    *,
    images: list[ChatImage] | None = None,
) -> str:
    """Один ход агента: ответ клиенту (текст). Исключений наружу не отдаём.

    images — фото клиента (эскиз/референс): уходят модели, если та
    мультимодальная; в истории остаётся текстовый факт.
    """
    async with _lock_for(client.id):
        return await _run_turn(session, business, client, text, images=images)


async def _run_turn(
    session: AsyncSession,
    business: Business,
    client: Client,
    text: str,
    *,
    images: list[ChatImage] | None = None,
) -> str:
    state = EscalationState()
    registry = booking_tool_registry(session, business, client, state)
    master_names = await _master_names_for_prompt(session, business.id)
    try:
        result = await chat(
            session,
            business,
            text,
            client_id=client.id,
            tools=BOOKING_TOOLS,
            tool_registry=registry,
            build_prompt=partial(build_booking_agent_prompt, master_names=master_names),
            use_data_question_nudge=False,
            images=images,
        )
    except Exception:
        logger.exception("Booking agent turn failed for client %s", client.id)
        try:
            await assignments.notify_owner_about_escalation(
                session, "—", client.full_name,
                f"Сбой агента записи; сообщение клиента: {text[:300]}",
            )
        except Exception:
            logger.exception("Failed to notify owner about booking agent failure")
        return FAILURE_CLIENT_REPLY

    if not state.escalated:
        return result.reply

    # Эскалировано: клиенту фиксированный текст (прощание агент уже сказал
    # в transcript, клиенту отправляем только единый понятный ответ).
    try:
        await assignments.notify_master_about_escalation(
            session, state.master_id, client.full_name, state.order_summary
        )
        await assignments.notify_owner_about_escalation(
            session, state.master_name or "—", client.full_name, state.order_summary
        )
    except Exception:
        logger.exception(
            "Failed to notify about escalation for client %s", client.id
        )
    return ESCALATION_CLIENT_REPLY
