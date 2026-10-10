"""Интеграционные тесты хода агента записи (run_booking_turn).

chat() подменяется: тест проверяет связку run_booking_turn ↔ assignment/
уведомления, а не сам LLM-луп.
"""

import pytest

from tg_studio.modules.ai import booking
from tg_studio.modules.ai.client import ChatResult
from tg_studio.modules.chat import assignments as assignments_mod
from tg_studio.modules.chat import service

from ..conftest import make_business, make_client, make_master


@pytest.fixture(autouse=True)
def fake_telegram(monkeypatch):
    """TG-отправку глушим (накопитель вызовов в списке)."""
    sent = []

    async def fake_send(telegram_id, text):
        sent.append((telegram_id, text))
        return 1

    monkeypatch.setattr(service, "send_text_to_telegram", fake_send)
    monkeypatch.setattr(assignments_mod, "send_text_to_telegram", fake_send)
    return sent


@pytest.fixture
async def business_and_master(db_session):
    business = await make_business(db_session)
    master = await make_master(db_session, business.id, full_name="Елена Лизунова")
    return business, master


@pytest.mark.asyncio
async def test_plain_reply(db_session, business_and_master, monkeypatch):
    business, master = business_and_master
    client = await make_client(db_session, telegram_id=610)

    async def fake_chat(session, business, user_message, **kwargs):
        assert kwargs["client_id"] == client.id
        assert kwargs["use_data_question_nudge"] is False
        return ChatResult(reply="Расскажите про идею", conversation_id=1, awaiting_confirmation=False)

    monkeypatch.setattr(booking, "chat", fake_chat)

    reply = await booking.run_booking_turn(db_session, business, client, "хочу тату")
    assert reply == "Расскажите про идею"
    assert await assignments_mod.get_open_assignment(db_session, client.id) is None


@pytest.mark.asyncio
async def test_escalation_sets_assignment_and_fixed_reply(
    db_session, business_and_master, monkeypatch, fake_telegram
):
    business, master = business_and_master
    client = await make_client(db_session, telegram_id=611)

    async def fake_chat(session, business, user_message, *, tool_registry=None, **kwargs):
        # модель вызвала тул эскалации, затем финальный текст
        result = await tool_registry["escalate_to_master"](
            business_id=business.id,
            master_name="Елена",
            order_summary="минимализм 5 см на предплечье",
        )
        assert result["status"] == "escalated"
        return ChatResult(reply="До связи!", conversation_id=1, awaiting_confirmation=False)

    monkeypatch.setattr(booking, "chat", fake_chat)
    sent = fake_telegram

    reply = await booking.run_booking_turn(db_session, business, client, "хочу к Елене")
    assert reply == booking.ESCALATION_CLIENT_REPLY

    assignment = await assignments_mod.get_open_assignment(db_session, client.id)
    assert assignment is not None
    assert assignment.master_id == master.id
    assert assignment.order_summary == "минимализм 5 см на предплечье"

    # уведомления: мастеру (tg id нет у мастера без user — в этом тесте его нет)
    # и владельцу (owner_telegram_id=99999). Проверяем тексты.
    owner_notifications = [t for _, t in sent if "закреплён за мастером" in t]
    assert owner_notifications, "владелец должен получить уведомление об эскалации"


@pytest.mark.asyncio
async def test_llm_failure_returns_failure_reply(
    db_session, business_and_master, monkeypatch, fake_telegram
):
    business, master = business_and_master
    client = await make_client(db_session, telegram_id=612)

    async def failing_chat(*args, **kwargs):
        raise RuntimeError("openrouter down")

    monkeypatch.setattr(booking, "chat", failing_chat)
    sent = fake_telegram

    reply = await booking.run_booking_turn(db_session, business, client, "хочу тату")
    assert reply == booking.FAILURE_CLIENT_REPLY
    assert any("Сбой агента" in t for _, t in sent)
    assert await assignments_mod.get_open_assignment(db_session, client.id) is None


@pytest.mark.asyncio
async def test_get_masters_info_lists_portfolio(db_session, business_and_master, tmp_path,
                                               monkeypatch):
    """get_masters_info возвращает мастеров с абсолютными URL портфолио."""
    from tg_studio.config import settings
    from tg_studio.db.models import MasterPortfolioFile
    from tg_studio.modules.ai.booking_tools import booking_tool_registry

    business, master = business_and_master
    client = await make_client(db_session, telegram_id=613)

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "api_public_url", "https://api.example.com")

    f = MasterPortfolioFile(
        master_id=master.id, stored_path="masters/portfolio/1/p1.jpg",
        original_name="p1.jpg", mime="image/jpeg", size_bytes=10,
    )
    db_session.add(f)
    await db_session.commit()

    state = booking.EscalationState()
    registry = booking_tool_registry(db_session, business, client, state)
    info = await registry["get_masters_info"](business_id=business.id)

    assert len(info["masters"]) == 1
    m = info["masters"][0]
    assert m["name"] == "Елена Лизунова"
    assert m["portfolio"] == [f"https://api.example.com/api/public/masters/{master.id}/portfolio/{f.id}"]


@pytest.mark.asyncio
async def test_conversation_persists_between_turns(db_session, business_and_master):
    """Клиентский агент продолжает одну беседу между сообщениями (и перезапусками)."""
    from tg_studio.db.models import AIConversation, AIMessage
    from tg_studio.modules.ai.client import _load_or_create_conversation

    business, master = business_and_master
    client = await make_client(db_session, telegram_id=614)

    conv = await _load_or_create_conversation(
        db_session, business.id, None, client_id=client.id
    )
    conv.messages.append(
        AIMessage(conversation_id=conv.id, role="user", content="хочу тату к Елене")
    )
    await db_session.commit()

    # Второй ход (как после перезапуска бота) — та же беседа, история на месте
    conv2 = await _load_or_create_conversation(
        db_session, business.id, None, client_id=client.id
    )
    assert conv2.id == conv.id
    assert [m.content for m in conv2.messages] == ["хочу тату к Елене"]

    # Другой клиент — своя беседа
    other = await make_client(db_session, telegram_id=615)
    conv3 = await _load_or_create_conversation(
        db_session, business.id, None, client_id=other.id
    )
    assert conv3.id != conv.id

    # У явного conversation_id старое поведение: чужой client_id — ошибка
    import pytest as _pytest
    from tg_studio.modules.ai.client import ConversationNotFoundError

    with _pytest.raises(ConversationNotFoundError):
        await _load_or_create_conversation(
            db_session, business.id, conv.id, client_id=other.id
        )
    count = (
        await db_session.execute(__import__("sqlalchemy").select(
            __import__("sqlalchemy").func.count()
        ).select_from(AIConversation))
    ).scalar_one()
    assert count == 2  # новый «клиент без беседы» получил свою, а не третью
