"""Юнит-тесты роутера сообщений (классификатор агент/мастер)."""

import pytest

from tg_studio.db.models import ChatDirection, ChatMessage
from tg_studio.modules.ai import router

from ..conftest import make_business, make_client


def test_parse_verdict():
    assert router.parse_verdict("MASTER") == router.ROUTE_MASTER
    assert router.parse_verdict("Master") == router.ROUTE_MASTER
    assert router.parse_verdict("  AI  ") == router.ROUTE_AGENT
    assert router.parse_verdict("Ответ: AI") == router.ROUTE_AGENT
    assert router.parse_verdict("не понимаю") is None
    assert router.parse_verdict("") is None
    assert router.parse_verdict(None) is None


def test_format_history_media_labels():
    msgs = [
        ChatMessage(
            client_id=1, direction=ChatDirection.from_client,
            telegram_file_id="fid", file_kind="photo",
        ),
        ChatMessage(
            client_id=1, direction=ChatDirection.from_client, content="хочу тату",
        ),
    ]
    text = router.format_history(msgs)
    assert "📷 Фото" in text
    assert "Клиент: хочу тату" in text


@pytest.mark.asyncio
async def test_no_assignment_routes_agent_without_llm(db_session, monkeypatch):
    """До первой эскалации LLM не дёргается — всё агенту."""
    business = await make_business(db_session)
    client = await make_client(db_session, telegram_id=501)

    async def boom(**kwargs):
        raise AssertionError("LLM не должен вызываться без открытого assignment")

    import tg_studio.modules.ai.client as ai_client

    class FakeOpenAI:
        chat = type("C", (), {"completions": boom})()

    monkeypatch.setattr(ai_client, "get_openai_client", lambda: FakeOpenAI())

    route = await router.classify_message(db_session, client.id, "хочу тату")
    assert route == router.ROUTE_AGENT


@pytest.mark.asyncio
async def test_open_assignment_llm_verdict_master(db_session, monkeypatch):
    from tg_studio.modules.chat import assignments as assignments_mod

    business = await make_business(db_session)
    client = await make_client(db_session, telegram_id=502)
    await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=1, order_summary="тест",
    )

    class FakeMsg:
        content = "MASTER"

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeCompletions:
        async def create(self, **kwargs):
            return FakeResp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        chat = FakeChat()

    import tg_studio.modules.ai.client as ai_client

    monkeypatch.setattr(ai_client, "get_openai_client", lambda: FakeOpenAI())

    route = await router.classify_message(db_session, client.id, "сколько стоит уход?")
    assert route == router.ROUTE_MASTER


@pytest.mark.asyncio
async def test_open_assignment_unparsable_falls_back_to_master(db_session, monkeypatch):
    """Сбой классификатора → мастер (не терять человека)."""
    from tg_studio.modules.chat import assignments as assignments_mod

    business = await make_business(db_session)
    client = await make_client(db_session, telegram_id=503)
    await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=1, order_summary="тест",
    )

    class FakeOpenAI:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise RuntimeError("openrouter down")

    import tg_studio.modules.ai.client as ai_client

    monkeypatch.setattr(ai_client, "get_openai_client", lambda: FakeOpenAI())

    route = await router.classify_message(db_session, client.id, "новая заявка")
    assert route == router.ROUTE_MASTER


@pytest.mark.asyncio
async def test_history_limited_to_ten(db_session):
    client = await make_client(db_session, telegram_id=504)
    for i in range(15):
        db_session.add(ChatMessage(
            client_id=client.id, direction=ChatDirection.from_client, content=str(i),
        ))
    await db_session.commit()

    history = await router.load_history(db_session, client.id)
    assert len(history) == 10
    # хронологический порядок: первые 5 отброшены
    assert history[0].content == "5"
    assert history[-1].content == "14"
