"""
AI chat orchestrator — manages the OpenAI conversation loop with function calling.
Tool calls execute immediately without user confirmation (read-only SQL is already validated).
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from tg_studio.config import settings
from tg_studio.db.models import AIConversation, AIMessage, Business

from .compression import (
    KEEP_RECENT_MESSAGES,
    needs_compression,
    summarise_messages,
)
from .openai_agent_loop import (
    looks_like_data_question,
    run_openai_analytics_loop,
    serialize_openai_tool_calls,
)
from .queries import TOOL_REGISTRY
from .system_prompt import (
    DATA_QUESTION_REQUIRES_TOOL_NUDGE,
    build_system_prompt,
)
from .tools import ANALYTICS_TOOLS

logger = logging.getLogger(__name__)

type AIEvHandler = Callable[[dict[str, Any]], Awaitable[None]] | None

MAX_TOOL_ROUNDS = 12

_openai_client: AsyncOpenAI | None = None


@dataclass
class ChatResult:
    reply: str
    conversation_id: int
    awaiting_confirmation: bool


def get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        kwargs: dict = {"api_key": settings.llm_api_key}
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        _openai_client = AsyncOpenAI(**kwargs)
    return _openai_client


class ConversationNotFoundError(Exception):
    pass


async def _load_or_create_conversation(
    session: AsyncSession,
    business_id: int,
    user_id: int,
    conversation_id: int | None,
) -> AIConversation:
    if conversation_id:
        conv = await session.get(
            AIConversation,
            conversation_id,
            options=[selectinload(AIConversation.messages)],
        )
        if conv and conv.business_id == business_id and conv.user_id == user_id:
            return conv
        raise ConversationNotFoundError(
            f"Conversation #{conversation_id} not found"
        )

    conv = AIConversation(business_id=business_id, user_id=user_id)
    session.add(conv)
    await session.flush()
    set_committed_value(conv, "messages", [])
    return conv


def _db_messages_to_openai(
    messages: list[AIMessage],
    summary: str | None,
) -> list[dict]:
    """Convert stored AIMessage rows to OpenAI message dicts.

    Ensures valid message sequencing:
    - assistant with tool_calls is only included WITH tool_calls if
      followed by tool response messages; otherwise treated as plain text.
    - tool messages are only included if preceded by an assistant with
      matching tool_calls in the output (orphaned tool msgs are dropped).
    """
    result: list[dict] = []

    if summary:
        result.append({
            "role": "user",
            "content": f"[Краткое резюме предыдущего разговора]:\n{summary}",
        })
        result.append({
            "role": "assistant",
            "content": "Понял, я помню контекст нашего предыдущего разговора. Чем могу помочь?",
        })

    last_had_tool_calls = False

    for i, msg in enumerate(messages):
        if msg.role == "tool":
            if not last_had_tool_calls:
                continue
            result.append({
                "role": "tool",
                "content": msg.content or "",
                "tool_call_id": msg.tool_call_id or "",
            })
            has_more_tools = (
                i + 1 < len(messages) and messages[i + 1].role == "tool"
            )
            if not has_more_tools:
                last_had_tool_calls = False
            continue

        last_had_tool_calls = False

        if msg.role == "assistant" and msg.tool_calls_json:
            has_tool_response = (
                i + 1 < len(messages) and messages[i + 1].role == "tool"
            )
            if has_tool_response:
                try:
                    tool_calls = json.loads(msg.tool_calls_json)
                except json.JSONDecodeError:
                    tool_calls = None
                if tool_calls:
                    result.append({
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": tool_calls,
                    })
                    last_had_tool_calls = True
                    continue
            if not msg.content:
                continue
            result.append({"role": "assistant", "content": msg.content})
        else:
            result.append({"role": msg.role, "content": msg.content or ""})

    return result


async def _execute_tool_call(
    business_id: int,
    name: str,
    arguments: dict,
) -> str:
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {name}"})

    safe_args = {k: v for k, v in arguments.items() if k != "business_id"}

    try:
        result = await fn(business_id=business_id, **safe_args)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        logger.exception("Tool %s failed", name)
        return json.dumps({"error": "Error executing data query"})




async def _maybe_compress(
    session: AsyncSession,
    client: AsyncOpenAI,
    conversation: AIConversation,
) -> None:
    if not needs_compression(len(conversation.messages)):
        return

    old_count = len(conversation.messages) - KEEP_RECENT_MESSAGES
    old_messages = conversation.messages[:old_count]

    openai_msgs = _db_messages_to_openai(old_messages, conversation.summary)
    new_summary = await summarise_messages(client, openai_msgs, conversation.summary)

    old_ids = [m.id for m in old_messages]
    await session.execute(
        delete(AIMessage).where(AIMessage.id.in_(old_ids))
    )

    conversation.summary = new_summary
    await session.flush()

    refreshed = await session.execute(
        select(AIMessage)
        .where(AIMessage.conversation_id == conversation.id)
        .order_by(AIMessage.created_at)
    )
    conversation.messages = list(refreshed.scalars().all())


async def _execute_pending_tools(
    session: AsyncSession,
    client: AsyncOpenAI,
    conversation: AIConversation,
    business: Business,
    *,
    on_event: AIEvHandler = None,
) -> ChatResult:
    """Execute pending tool calls and run the analytics loop."""
    pending_msg = None
    pending_idx = -1
    for i, msg in enumerate(conversation.messages):
        if msg.role == "assistant" and msg.tool_calls_json:
            pending_msg = msg
            pending_idx = i

    if not pending_msg:
        await session.commit()
        return ChatResult(
            reply="Нет ожидающих запросов. Задайте новый вопрос.",
            conversation_id=conversation.id,
            awaiting_confirmation=False,
        )

    tool_calls_data = json.loads(pending_msg.tool_calls_json)

    # Build message list: history BEFORE the tool_calls msg (cleaned),
    # then the tool_calls msg itself (manually, so it keeps tool_calls).
    history_before = conversation.messages[:pending_idx]

    system_prompt = build_system_prompt(business)
    openai_messages = [
        {"role": "system", "content": system_prompt},
        *_db_messages_to_openai(history_before, conversation.summary),
        {
            "role": "assistant",
            "content": pending_msg.content,
            "tool_calls": tool_calls_data,
        },
    ]

    # Batch execute initial tool calls and collect results
    tool_results = []
    for tc in tool_calls_data:
        args = json.loads(tc["function"]["arguments"])
        tool_result = await _execute_tool_call(
            business.id, tc["function"]["name"], args
        )

        tool_msg = AIMessage(
            conversation_id=conversation.id,
            role="tool",
            content=tool_result,
            tool_call_id=tc["id"],
        )
        session.add(tool_msg)
        conversation.messages.append(tool_msg)

        tool_results.append({
            "role": "tool",
            "content": tool_result,
            "tool_call_id": tc["id"],
        })

    # Batch flush all tool messages at once
    await session.flush()

    openai_messages.extend(tool_results)

    async def _tool(name: str, arguments: dict) -> str:
        return await _execute_tool_call(business.id, name, arguments)

    result = await run_openai_analytics_loop(
        client,
        openai_messages,
        execute_tool=_tool,
        max_tool_rounds=MAX_TOOL_ROUNDS,
        on_event=on_event,
    )
    for p in result.pending_db_messages:
        row = AIMessage(
            conversation_id=conversation.id,
            role=p.role,
            content=p.content,
            tool_calls_json=p.tool_calls_json,
            tool_call_id=p.tool_call_id,
        )
        session.add(row)
        conversation.messages.append(row)

    # Batch flush all loop messages at once
    await session.flush()

    if result.stopped_at_max_rounds:
        fallback = "Извините, не удалось получить ответ. Попробуйте переформулировать вопрос."
        session.add(
            AIMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=fallback,
            )
        )
        await session.commit()
        return ChatResult(
            reply=fallback,
            conversation_id=conversation.id,
            awaiting_confirmation=False,
        )

    await _maybe_compress(session, client, conversation)
    await session.commit()
    return ChatResult(
        reply=result.final_reply,
        conversation_id=conversation.id,
        awaiting_confirmation=False,
    )


async def chat(
    session: AsyncSession,
    business: Business,
    user_id: int,
    user_message: str,
    conversation_id: int | None = None,
    confirmed: bool = False,  # noqa: ARG001 — kept for API compatibility
    *,
    on_event: AIEvHandler = None,
) -> ChatResult:
    """
    Main entry point.
    Tool calls execute immediately — no confirmation flow needed since SQL is read-only and validated.
    """
    client = get_openai_client()
    conversation = await _load_or_create_conversation(
        session, business.id, user_id, conversation_id
    )

    # Reset any stale awaiting state
    if conversation.awaiting_confirmation:
        conversation.awaiting_confirmation = False

    user_msg = AIMessage(
        conversation_id=conversation.id,
        role="user",
        content=user_message,
    )
    session.add(user_msg)
    conversation.messages.append(user_msg)

    system_prompt = build_system_prompt(business)
    openai_messages = [
        {"role": "system", "content": system_prompt},
        *_db_messages_to_openai(conversation.messages, conversation.summary),
    ]

    response = await client.chat.completions.create(
        model=settings.llm_model,
        temperature=0,
        messages=openai_messages,
        tools=ANALYTICS_TOOLS,
    )
    choice = response.choices[0]

    if (
        not (choice.finish_reason == "tool_calls" or choice.message.tool_calls)
        and looks_like_data_question(user_message)
    ):
        logger.warning(
            "LLM ответила без вызова execute_analytics_sql на похожий на данные вопрос — "
            "повтор с tool_choice=required | сообщение=%r",
            user_message[:200],
        )
        # Retry with stronger tool requirement but avoid full duplicate context
        response = await client.chat.completions.create(
            model=settings.llm_model,
            temperature=0,
            messages=[
                *openai_messages,
                {"role": "system", "content": DATA_QUESTION_REQUIRES_TOOL_NUDGE},
            ],
            tools=ANALYTICS_TOOLS,
            tool_choice="required",
        )
        choice = response.choices[0]

    # AI wants to call tools — execute immediately
    if choice.finish_reason == "tool_calls" or choice.message.tool_calls:
        assistant_msg = AIMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=choice.message.content,
            tool_calls_json=serialize_openai_tool_calls(choice.message.tool_calls),
        )
        session.add(assistant_msg)
        await session.flush()
        conversation.messages.append(assistant_msg)

        return await _execute_pending_tools(
            session, client, conversation, business, on_event=on_event
        )

    # AI answered directly without needing tools (e.g. off-topic refusal, clarification)
    reply_text = choice.message.content or ""
    assistant_reply_msg = AIMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=reply_text,
    )
    session.add(assistant_reply_msg)
    await session.flush()
    conversation.messages.append(assistant_reply_msg)

    await _maybe_compress(session, client, conversation)
    await session.commit()

    return ChatResult(
        reply=reply_text,
        conversation_id=conversation.id,
        awaiting_confirmation=False,
    )
