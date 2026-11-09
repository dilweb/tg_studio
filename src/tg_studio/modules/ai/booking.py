"""Ход агента записи в TG-боте: один входящий текст → один ответ.

Переиспользует generic-луп ai/client.chat() с booking-тулами. При
эскалации луп останавливается по флагу в registry (EscalationState):
клиенту уходит фиксированный прощальный текст, мастеру и владельцу —
уведомления. Пер-клиентский lock от параллельных ходов на быстрые
сообщения подряд.
"""

import asyncio
import logging
import re
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

# Таймаут захвата per-client lock: зависший ход (долгий/ушедший в таймаут
# LLM-вызов) не должен вечно блокировать следующие сообщения клиента
LOCK_ACQUIRE_TIMEOUT_SECONDS = 180.0

# Фразы, которыми модель заявляет о передаче кейса мастеру без реального вызова
# escalate_to_master («он уже получил вашу заявку», «я передал заказ мастеру»).
# Единственный настоящий механизм эскалации — флаг EscalationState, который
# ставит только вызов тула; при эскалации клиенту уходит фиксированный текст,
# а свободный ответ модели не показывается вовсе. Когда эскалации не было,
# такой текст — выдумка, вырезаем его детерминированно, не надеясь на промпт.
_FAKE_HANDOVER_RE = re.compile(
    r"получ\w*\s+(вашу\s+)?заявк\w+"                       # «получил вашу заявку»
    r"|уже\s+получ\w+"                                     # «уже получил»
    r"|я\s+уже\s+(передал\w*|отправил\w*|уведомил\w*)"    # «я уже передал/отправил/уведомил»
    r"|я\s+передал\w*(\s+ваш\w*)?\s+(заказ\w*|заявк\w*|запрос\w*|вас|мастеру)"
    r"|я\s+(передам|отправлю|уведомлю)\s+(ваш\w*\s+)?(заказ\w*|заявк\w*|запрос\w*|вопрос\w*)(\s+мастеру)?"
    r"|я\s+отправил\w*\s+(ваш\w*\s+)?(заказ\w*|заявк\w*|запрос\w*)"
    r"|я\s+уведомил\w*"
    r"|я\s+свяжу\w*\s+вас\s+с\s+мастером"
    r"|(мы|я)\s+свяжемся\s+с\s+вами"
    r"|передал\w*\s+(ваш\w*\s+)?(заказ\w*|заявк\w*|запрос\w*)\s+мастеру"
    r"|уведомил\w*\s+мастера"
    r"|мастер\w*\s+(уже\s+)?(напишет|свяжется|позвонит|ответит)"
    r"|(заявка|заказ|запрос)\s+(уже\s+)?(отправлен\w*|передан\w*|ушёл|ушла)"
    r"|already\s+(got|received|passed|sent|forwarded|notified)"
    r"|the\s+master\s+(will|has)\s+(contact|write|call|reply|get back)"
    r"|i\s+(have\s+|already\s+)?(passed|sent|forwarded|notified)"
    r"|handed\s+(over|off)",
    re.IGNORECASE,
)

_NO_HANDOVER_FALLBACK = "Хорошо! Если будут ещё вопросы — пишите 😊"


def _strip_fake_handover_claims(reply: str, *, client_id: int) -> str:
    """Вырезает из свободного ответа заявления о передаче мастеру, которой не было.

    Вызывается только когда эскалации не произошло (state.escalated == False):
    тогда единственный корректный «ответ мастера» — фиксированный текст хода,
    а свободный ответ модели уходит клиенту как есть.
    """
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", reply.strip()) if s]
    kept = [s for s in sentences if not _FAKE_HANDOVER_RE.search(s)]
    if len(kept) == len(sentences):
        return reply
    logger.warning(
        "Booking agent claimed a handover without escalate_to_master "
        "(client %s); stripped from reply: %r",
        client_id, reply,
    )
    scrubbed = " ".join(kept).strip()
    return scrubbed or _NO_HANDOVER_FALLBACK


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

    images — фото клиента (эскиз/референс): сначала отдельный вызов снимает
    с них текстовый анализ, сам ход идёт без картинки (fallback при сбое
    экстракции — фото напрямую); в истории остаётся факт + анализ текстом.
    """
    lock = _lock_for(client.id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=LOCK_ACQUIRE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(
            "Booking turn for client %s: lock acquire timed out after %.0fs",
            client.id,
            LOCK_ACQUIRE_TIMEOUT_SECONDS,
        )
        return FAILURE_CLIENT_REPLY
    try:
        return await _run_turn(session, business, client, text, images=images)
    finally:
        lock.release()


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
        # Эскалации не было — значит, никакой передачи мастеру не существует;
        # вырезаем из свободного ответа выдуманные заявления о ней.
        return _strip_fake_handover_claims(result.reply, client_id=client.id)

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
