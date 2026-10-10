"""Тулы агента записи: метаданные мастеров, занятость по дням и эскалация.

Registry строится замыканием на текущий ход (сессия, клиент) и держит
EscalationState — флаг эскалации. Луп останавливается по флагу (в
run_booking_turn), а не исключением: _execute_tool_call не оборачивает
исключения, а структура tool-ответов должна остаться валидной.
"""

import difflib
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.db.models import Business, Client, Master
from tg_studio.modules.ai.calendar_tools import get_bookings
from tg_studio.modules.business import portfolio_files
from tg_studio.modules.chat import assignments

logger = logging.getLogger(__name__)

FUZZY_MATCH_THRESHOLD = 0.6
# Насколько близко к лучшему должен быть кандидат, чтобы считаться «таким же
# похожим»: имена-близнецы (Диляра/Дилявер) дают почти равные ratios —
# оба попадают в ambiguous вместо случайного выбора
AMBIGUITY_GAP = 0.03


@dataclass
class EscalationState:
    """Флаг остановки лупа + данные эскалации для уведомлений."""

    escalated: bool = False
    master_id: int | None = None
    master_name: str | None = None
    order_summary: str | None = None


BOOKING_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_masters_info",
            "description": (
                "List of the studio's active masters: name, specializations, "
                "description and portfolio photo URLs. Call it whenever the client "
                "asks who the masters are or who they could book with, and when "
                "helping the client choose. Include portfolio links as bare URLs, "
                "one per line — they are delivered to the client as actual photos."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [],
                           "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bookings",
            "description": (
                "Busy intervals of the studio's masters from the live calendar, "
                "to answer 'when could I come in'. Returns BUSY intervals only "
                "(master, start, end, label) — day-level workload, not free slots. "
                "Present the result to the client as day-level workload "
                "(e.g. 'on Saturday two bookings, on Friday the master is free'), "
                "NEVER as exact free times, and always add that the final date "
                "and time are agreed personally with the master. Optional "
                "filters: master (fuzzy name like 'anna'), date_from/date_to "
                "(YYYY-MM-DD); default window is the next 2 weeks. Errors come "
                "back as {'error': ...}: master_not_found (with available_masters), "
                "ambiguous_master, calendar_not_connected — handle them in the reply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "master": {
                        "type": "string",
                        "description": "Master name, fuzzy match is fine ('anna' matches 'Anna Smirnova').",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start of the window, YYYY-MM-DD.",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End of the window, YYYY-MM-DD.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_master",
            "description": (
                "Hand the collected order to a specific master: the chat is pinned "
                "to them and they will personally continue the conversation. Call it "
                "once, when the client decided on a master (or the order is complete "
                "enough). After a successful call say one goodbye phrase — you will "
                "not reply anymore."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "master_name": {
                        "type": "string",
                        "description": (
                            "Master name — copy exactly from the 'Studio masters' "
                            "list in the system prompt. If the tool returns "
                            "ambiguous_master, ask the client which master they mean."
                        ),
                    },
                    "order_summary": {
                        "type": "string",
                        "description": (
                            "Short order summary: idea, size, placement, style, "
                            "anything else the client said."
                        ),
                    },
                },
                "required": ["master_name", "order_summary"],
                "additionalProperties": False,
            },
        },
    },
]


def _normalize(name: str) -> str:
    return " ".join(name.casefold().split())


def match_masters(name: str, masters: list[Master]) -> list[Master]:
    """Кандидаты на имя клиента. Пусто — не нашли; больше одного — имя
    неоднозначно, тула вернёт список и агент переспросит клиента.

    Каскад: точное совпадение → подстрока → difflib ≥ 0.6. На неоднозначность
    проверяется каждый уровень: несколько «диля…» не выбирают лучшего наугад.
    """
    needle = _normalize(name)
    if not needle:
        return []
    haystacks = [(m, _normalize(m.full_name)) for m in masters]

    exact = [m for m, hay in haystacks if hay == needle]
    if exact:
        return exact

    substr = [m for m, hay in haystacks if needle in hay or hay in needle]
    if substr:
        return substr

    scored = [
        (difflib.SequenceMatcher(None, needle, hay).ratio(), m)
        for m, hay in haystacks
    ]
    best_ratio = max((r for r, _ in scored), default=0.0)
    if best_ratio < FUZZY_MATCH_THRESHOLD:
        return []
    return [m for r, m in scored if best_ratio - r <= AMBIGUITY_GAP]


def booking_tool_registry(
    session: AsyncSession,
    business: Business,
    client: Client,
    state: EscalationState,
) -> dict:
    """Тулы одного хода агента: замыкание на сессию/клиента и состояние эскалации."""

    async def get_masters_info(business_id: int) -> dict:
        result = await session.execute(
            select(Master).where(
                Master.business_id == business_id, Master.is_active.is_(True)
            )
        )
        masters = list(result.scalars().all())
        out = []
        for m in masters:
            portfolio = [
                portfolio_files.portfolio_url(f)
                for f in await _portfolio_for(session, m.id)
            ]
            out.append(
                {
                    "name": m.full_name,
                    "specializations": m.specializations or [],
                    "description": m.description or "",
                    "avatar": portfolio_files.avatar_url(m),
                    "portfolio": portfolio,
                }
            )
        return {"masters": out}

    async def escalate_to_master(business_id: int, master_name: str, order_summary: str) -> dict:
        result = await session.execute(
            select(Master).where(
                Master.business_id == business_id, Master.is_active.is_(True)
            )
        )
        masters = list(result.scalars().all())
        candidates = match_masters(master_name, masters)
        if not candidates:
            return {
                "error": "master_not_found",
                "available": [m.full_name for m in masters],
            }
        if len(candidates) > 1:
            return {
                "error": "ambiguous_master",
                "candidates": [m.full_name for m in candidates],
            }
        found = candidates[0]
        assignment = await assignments.create_assignment(
            session,
            business_id=business_id,
            client_id=client.id,
            master_id=found.id,
            order_summary=order_summary,
        )
        state.escalated = True
        state.master_id = found.id
        state.master_name = found.full_name
        state.order_summary = order_summary
        logger.info(
            "Client %s escalated to master %s (assignment %s)",
            client.id,
            found.id,
            assignment.id,
        )
        return {"status": "escalated", "master": found.full_name}

    return {
        "get_masters_info": get_masters_info,
        # Тул календаря из ai/calendar_tools: контракт совместим — lуп
        # подставляет business_id kwarg, сигнатура уже его принимает.
        "get_bookings": get_bookings,
        "escalate_to_master": escalate_to_master,
    }


async def _portfolio_for(session: AsyncSession, master_id: int):
    from tg_studio.db.models import MasterPortfolioFile

    result = await session.execute(
        select(MasterPortfolioFile).where(MasterPortfolioFile.master_id == master_id)
    )
    return list(result.scalars().all())
