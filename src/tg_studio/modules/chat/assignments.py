"""Закрепление чата клиента за мастером (эскалация AI-агентом).

Чат закрепляется при эскалации: клиенту больше отвечает не агент, а
мастер из миниаппы «Чаты». Открытых закреплений на клиента не более
одного — create_assignment() закрывает прежние.
"""

import logging
from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.config import settings
from tg_studio.db.models import (
    ChatAssignment,
    ChatAssignmentStatus,
    Master,
    User,
)
from tg_studio.modules.chat.service import send_text_to_telegram

logger = logging.getLogger(__name__)


async def get_open_assignment(
    session: AsyncSession, client_id: int
) -> ChatAssignment | None:
    result = await session.execute(
        select(ChatAssignment)
        .where(
            ChatAssignment.client_id == client_id,
            ChatAssignment.status == ChatAssignmentStatus.open,
        )
        .order_by(ChatAssignment.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_assignment(
    session: AsyncSession,
    *,
    business_id: int,
    client_id: int,
    master_id: int,
    order_summary: str | None,
) -> ChatAssignment:
    """Закрепить чат за мастером; прежние открытые закрываем."""
    now_open = await session.execute(
        select(ChatAssignment).where(
            ChatAssignment.client_id == client_id,
            ChatAssignment.status == ChatAssignmentStatus.open,
        )
    )
    for row in now_open.scalars().all():
        row.status = ChatAssignmentStatus.closed
        row.closed_at = datetime.now()

    assignment = ChatAssignment(
        business_id=business_id,
        client_id=client_id,
        master_id=master_id,
        status=ChatAssignmentStatus.open,
        order_summary=order_summary,
    )
    session.add(assignment)
    await session.commit()
    return assignment


async def close_assignment(session: AsyncSession, assignment: ChatAssignment) -> None:
    assignment.status = ChatAssignmentStatus.closed
    assignment.closed_at = datetime.now()
    await session.commit()


async def _master_telegram_id(session: AsyncSession, master: Master) -> int | None:
    """master.telegram_id, fallback master.user_id → User.telegram_id."""
    if master.telegram_id:
        return master.telegram_id
    if master.user_id:
        user = await session.get(User, master.user_id)
        if user is not None and user.telegram_id:
            return user.telegram_id
    return None


async def notify_master_about_escalation(
    session: AsyncSession,
    master_id: int,
    client_name: str,
    order_summary: str | None,
) -> None:
    """TG-уведомление мастеру о закреплённом клиенте — best-effort.

    Короткое, без резюме заказа: вся история чата в миниаппе, общение
    только там.
    """
    master = await session.get(Master, master_id)
    if master is None:
        return
    telegram_id = await _master_telegram_id(session, master)
    if not telegram_id:
        logger.info(
            "Master %s has no telegram — no escalation notification", master.id
        )
        return
    miniapp = settings.miniapp_url.rstrip("/")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="📋 Открыть Mini App", web_app={"url": miniapp}
            ),
        ]]
    )
    text = f"🤖 Новый чат с клиентом {client_name}"
    try:
        await send_text_to_telegram(telegram_id, text, reply_markup=keyboard)
    except Exception:
        logger.exception(
            "Failed to notify master %s about escalation", master.id
        )


async def notify_owner_about_escalation(
    session: AsyncSession,
    master_name: str,
    client_name: str,
    order_summary: str | None,
) -> None:
    """Уведомление владельцу об эскалации — best-effort."""
    from tg_studio.modules.chat.service import resolve_owner_telegram_id

    owner_tg_id = await resolve_owner_telegram_id(session)
    if owner_tg_id is None:
        logger.warning("No owner to notify about escalation")
        return
    text = (
        f"🤖 Клиент закреплён за мастером\n"
        f"Мастер: {master_name}\n"
        f"Клиент: {client_name}\n"
        f"Заказ: {order_summary or '—'}"
    )
    try:
        await send_text_to_telegram(owner_tg_id, text)
    except Exception:
        logger.exception("Failed to notify owner about escalation")
