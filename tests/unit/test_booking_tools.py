"""Юнит-тесты тулов агента записи: матчинг имён и эскалация."""

import pytest

from tg_studio.db.models import ChatAssignment, ChatAssignmentStatus
from tg_studio.modules.ai.booking_tools import EscalationState, match_masters
from tg_studio.modules.chat import assignments as assignments_mod

from ..conftest import make_business, make_client, make_master


class FakeMaster:
    def __init__(self, full_name):
        self.full_name = full_name


def test_match_exact():
    masters = [FakeMaster("Елена Лизунова"), FakeMaster("Евгений Смирнов")]
    assert match_masters("елена лизунова", masters) == [masters[0]]


def test_match_substring():
    masters = [FakeMaster("Елена Лизунова"), FakeMaster("Евгений Смирнов")]
    assert match_masters("Евгений", masters) == [masters[1]]
    assert match_masters("лизунова", masters) == [masters[0]]


def test_match_fuzzy():
    masters = [FakeMaster("Елена Лизунова"), FakeMaster("Евгений Смирнов")]
    # typo, но SequenceMatcher ≥ 0.6
    assert match_masters("Евгений Смирнёв", masters) == [masters[1]]


def test_match_miss_returns_empty():
    masters = [FakeMaster("Елена Лизунова")]
    assert match_masters("Акакий", masters) == []
    assert match_masters("", masters) == []


def test_match_similar_names_ambiguous():
    """Диляра/Дилявер: каждый уровень каскада не выбирает наугад."""
    dilyara = FakeMaster("Диляра")
    dilyaver = FakeMaster("Дилявер")
    masters = [dilyara, dilyaver]
    # Точные имена различаются
    assert match_masters("Диляра", masters) == [dilyara]
    assert match_masters("дилявер", masters) == [dilyaver]
    # С typo срабатывает уникальная подстрока
    assert match_masters("Диляр", masters) == [dilyara]
    assert match_masters("Диляв", masters) == [dilyaver]
    # А вот неоднозначный префикс возвращает ОБА — агент переспросит
    assert match_masters("Дил", masters) == masters


@pytest.mark.asyncio
async def test_escalate_creates_open_assignment(db_session):
    business = await make_business(db_session)
    master = await make_master(db_session, business.id, full_name="Елена Лизунова")
    client = await make_client(db_session, telegram_id=510)

    from tg_studio.modules.ai.booking_tools import booking_tool_registry

    state = EscalationState()
    registry = booking_tool_registry(db_session, business, client, state)

    result = await registry["escalate_to_master"](
        business_id=business.id, master_name="елена", order_summary="минимализм 5 см"
    )
    assert result == {"status": "escalated", "master": "Елена Лизунова"}
    assert state.escalated is True
    assert state.master_id == master.id
    assert state.order_summary == "минимализм 5 см"

    assignment = await assignments_mod.get_open_assignment(db_session, client.id)
    assert assignment is not None
    assert assignment.master_id == master.id
    assert assignment.order_summary == "минимализм 5 см"


@pytest.mark.asyncio
async def test_escalate_unknown_master_returns_available(db_session):
    business = await make_business(db_session)
    await make_master(db_session, business.id, full_name="Елена Лизунова")
    client = await make_client(db_session, telegram_id=511)

    from tg_studio.modules.ai.booking_tools import booking_tool_registry

    state = EscalationState()
    registry = booking_tool_registry(db_session, business, client, state)

    result = await registry["escalate_to_master"](
        business_id=business.id, master_name="Акакий", order_summary="x"
    )
    assert result["error"] == "master_not_found"
    assert result["available"] == ["Елена Лизунова"]
    assert state.escalated is False


@pytest.mark.asyncio
async def test_escalate_skips_inactive_master(db_session):
    business = await make_business(db_session)
    await make_master(db_session, business.id, full_name="Старый Мастер", is_active=False)
    await make_master(db_session, business.id, full_name="Активный Мастер")
    client = await make_client(db_session, telegram_id=512)

    from tg_studio.modules.ai.booking_tools import booking_tool_registry

    state = EscalationState()
    registry = booking_tool_registry(db_session, business, client, state)

    result = await registry["escalate_to_master"](
        business_id=business.id, master_name="старый", order_summary="x"
    )
    assert result["error"] == "master_not_found"


@pytest.mark.asyncio
async def test_create_assignment_closes_previous_open(db_session):
    business = await make_business(db_session)
    m1 = await make_master(db_session, business.id, full_name="Мастер Один")
    m2 = await make_master(db_session, business.id, full_name="Мастер Два")
    client = await make_client(db_session, telegram_id=513)

    a1 = await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=m1.id, order_summary="первый",
    )
    a2 = await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=m2.id, order_summary="второй",
    )

    await db_session.refresh(a1)
    assert a1.status == ChatAssignmentStatus.closed
    assert a2.status == ChatAssignmentStatus.open
    open_rows = (
        (await db_session.execute(
            __import__("sqlalchemy").select(ChatAssignment).where(
                ChatAssignment.client_id == client.id,
                ChatAssignment.status == ChatAssignmentStatus.open,
            )
        ))
        .scalars()
        .all()
    )
    assert len(open_rows) == 1


@pytest.mark.asyncio
async def test_escalate_ambiguous_master_returns_candidates(db_session):
    business = await make_business(db_session)
    await make_master(db_session, business.id, full_name="Диляра")
    await make_master(db_session, business.id, full_name="Дилявер")
    client = await make_client(db_session, telegram_id=514)

    from tg_studio.modules.ai.booking_tools import booking_tool_registry

    state = EscalationState()
    registry = booking_tool_registry(db_session, business, client, state)

    result = await registry["escalate_to_master"](
        business_id=business.id, master_name="Дил", order_summary="x"
    )
    assert result == {"error": "ambiguous_master", "candidates": ["Диляра", "Дилявер"]}
    assert state.escalated is False
