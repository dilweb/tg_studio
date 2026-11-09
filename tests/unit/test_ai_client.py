"""ai client: сжатие контекста — best-effort (сбой не роняет ответ клиента)."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tg_studio.db.models import AIConversation, AIMessage
from tg_studio.modules.ai import client as ai_client

from ..conftest import make_business


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.mark.asyncio
async def test_maybe_compress_failure_keeps_history(db_session, monkeypatch):
    """Сбой summarise не роняет _maybe_compress: история остаётся несжатой."""
    messages = [
        SimpleNamespace(
            id=i + 1, role="user", content=f"msg {i}",
            tool_calls_json=None, tool_call_id=None,
        )
        for i in range(35)
    ]
    conversation = SimpleNamespace(id=1, summary=None, messages=messages)

    async def failing_summarise(client, msgs, existing_summary=None):
        raise RuntimeError("llm down")

    monkeypatch.setattr(ai_client, "summarise_messages", failing_summarise)

    await ai_client._maybe_compress(db_session, object(), conversation)  # не бросает
    assert len(conversation.messages) == 35
    assert conversation.summary is None


@pytest.mark.asyncio
async def test_maybe_compress_success(db_session, business, monkeypatch):
    """Успех: старая история удалена, остались KEEP_RECENT_MESSAGES + summary."""
    conv = AIConversation(business_id=business.id)
    db_session.add(conv)
    await db_session.flush()
    for i in range(35):
        db_session.add(AIMessage(conversation_id=conv.id, role="user", content=f"m{i}"))
    await db_session.commit()

    # Свежий SELECT с eager load: session.get вернул бы объект из identity map
    # без применения options, и conv.messages ушёл бы в lazy-load (MissingGreenlet).
    conv_id = conv.id
    conv = (
        await db_session.execute(
            select(AIConversation)
            .where(AIConversation.id == conv_id)
            .options(selectinload(AIConversation.messages))
        )
    ).scalar_one()

    async def fake_summarise(client, msgs, existing_summary=None):
        return "Краткое резюме"

    monkeypatch.setattr(ai_client, "summarise_messages", fake_summarise)

    await ai_client._maybe_compress(db_session, object(), conv)
    assert conv.summary == "Краткое резюме"
    assert len(conv.messages) == 10  # KEEP_RECENT_MESSAGES

    rows = (
        await db_session.execute(
            select(AIMessage).where(AIMessage.conversation_id == conv.id)
        )
    ).scalars().all()
    assert len(rows) == 10
