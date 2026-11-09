"""
AI chat orchestrator — manages the OpenAI conversation loop with function calling.
Tool calls execute immediately without user confirmation (read-only SQL is already validated).
"""

import base64
import inspect
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
from .llm_retry import chat_completion_with_retry
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
from .tools import AGENT_TOOLS

logger = logging.getLogger(__name__)

type AIEvHandler = Callable[[dict[str, Any]], Awaitable[None]] | None

MAX_TOOL_ROUNDS = 12

_openai_client: AsyncOpenAI | None = None


@dataclass
class ChatResult:
    reply: str
    conversation_id: int
    awaiting_confirmation: bool


@dataclass
class ChatImage:
    """Картинка от пользователя (эскиз, референс): байты + mime.

    Сначала describe_image снимает с неё текстовый анализ; в основной ход
    байты не идут (fallback при сбое экстракции — напрямую как image_url
    с data-URI). В ai_messages хранится только текст: факт + анализ.
    """

    data: bytes
    mime: str = "image/jpeg"
    description: str | None = None  # подпись клиента, идёт текстом перед фото


def _user_content(
    text: str, images: list[ChatImage] | None
) -> str | list[dict[str, Any]]:
    """Контент user-сообщения: строка, а с фото — multipart-массив
    (OpenAI-совместимый vision-формат, data-URI вместо загрузки файлов)."""
    if not images:
        return text
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for img in images:
        b64 = base64.b64encode(img.data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{img.mime};base64,{b64}"},
            }
        )
    return parts


def get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        kwargs: dict = {"api_key": settings.llm_api_key}
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        # Таймаут одного вызова: дефолт SDK 600с позволял зависшему вызову
        # часами держать per-client lock хода агента
        kwargs["timeout"] = settings.llm_timeout_seconds
        _openai_client = AsyncOpenAI(**kwargs)
    return _openai_client


def assistant_model() -> str:
    """Модель хода ассистента: мультимодальная (settings.assistant_model),
    если задана, иначе llm_model."""
    return settings.assistant_model


PHOTO_ANALYSIS_PROMPT = """You are a senior tattoo artist looking at a photo \
a client sent to a tattoo studio's booking assistant. Write a detailed SERVICE \
DESCRIPTION of the image in Russian: another assistant will consult the client \
using ONLY your text — it will never see the photo itself.

Cover what a master needs to plan and price the session:
- the exact style with tattoo terminology (блэкворк, неотрэдишнл, орнаментал, \
дотворк, лайнворк, реализм, флэш, гравюра… — "минимализм" only when it truly \
is just that);
- technique: line weight, solid fill vs shading/dotwork, colour palette and \
accents, whether the design looks fresh or aged;
- composition and motifs: what is depicted, layout, approximate size relative \
to the body area if it is visible;
- suggested body placement if the photo shows one;
- the client's caption (if any) is their own words about the request.

Be factual, 4-8 sentences, plain text without any markdown formatting. If the \
photo is not tattoo-related (a meme, a screenshot, a selfie without a sketch), \
say in one or two sentences what it actually shows instead."""


async def describe_image(images: list[ChatImage]) -> str | None:
    """Отдельный мультимодальный вызов: подробное описание фото текстом.

    Основной ход агента идёт без картинки — в контекст попадает только этот
    текст (ноль image-токенов ни в ходе, ни в раундах тулов). None —
    экстракция не удалась: фото тогда уходит модели напрямую (fallback).
    """
    parts: list[dict[str, Any]] = []
    for img in images:
        if img.description:
            parts.append({"type": "text", "text": img.description})
        b64 = base64.b64encode(img.data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{img.mime};base64,{b64}"},
            }
        )
    try:
        response = await chat_completion_with_retry(
            get_openai_client(),
            model=assistant_model(),
            temperature=0,
            messages=[
                {"role": "system", "content": PHOTO_ANALYSIS_PROMPT},
                {"role": "user", "content": parts},
            ],
        )
    except Exception:
        logger.exception("Photo analysis call failed")
        return None
    return (response.choices[0].message.content or "").strip() or None


class ConversationNotFoundError(Exception):
    pass


async def _load_or_create_conversation(
    session: AsyncSession,
    business_id: int,
    conversation_id: int | None,
    *,
    user_id: int | None = None,
    client_id: int | None = None,
) -> AIConversation:
    if conversation_id:
        conv = await session.get(
            AIConversation,
            conversation_id,
            options=[selectinload(AIConversation.messages)],
        )
        if (
            conv
            and conv.business_id == business_id
            and conv.user_id == user_id
            and conv.client_id == client_id
        ):
            return conv
        raise ConversationNotFoundError(
            f"Conversation #{conversation_id} not found"
        )

    # Без явного conversation_id клиентский агент продолжает последнюю беседу
    # клиента: история хранится в ai_messages и переживает перезапуски
    if client_id is not None:
        result = await session.execute(
            select(AIConversation)
            .options(selectinload(AIConversation.messages))
            .where(
                AIConversation.business_id == business_id,
                AIConversation.client_id == client_id,
            )
            .order_by(AIConversation.id.desc())
            .limit(1)
        )
        conv = result.scalar_one_or_none()
        if conv is not None:
            return conv

    conv = AIConversation(business_id=business_id, user_id=user_id, client_id=client_id)
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
    tool_registry: dict[str, Callable],
) -> str:
    fn = tool_registry.get(name)
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

    # Сжатие — оптимизация, не часть ответа: сбой LLM/БД не должен ронять
    # уже полученный ответ клиента — история просто останется несжатой
    try:
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
    except Exception:
        logger.exception(
            "Сжатие контекста не удалось — отвечаем без него (история не сжата)"
        )


async def _execute_pending_tools(
    session: AsyncSession,
    client: AsyncOpenAI,
    conversation: AIConversation,
    business: Business,
    *,
    tools: list[dict],
    tool_registry: dict[str, Callable],
    build_prompt: Callable[[Business], str],
    on_event: AIEvHandler = None,
    images: list[ChatImage] | None = None,
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

    try:
        tool_calls_data = json.loads(pending_msg.tool_calls_json)
        if not isinstance(tool_calls_data, list):
            raise ValueError("tool_calls is not a list")
    except (ValueError, TypeError):
        # Битый JSON в tool_calls (обрезанный ответ модели) — не роняем ход,
        # просим задать вопрос ещё раз
        logger.exception(
            "Broken tool_calls JSON in message %s — asking to retry", pending_msg.id
        )
        await session.commit()
        return ChatResult(
            reply="Не удалось разобрать ответ модели — задайте вопрос ещё раз.",
            conversation_id=conversation.id,
            awaiting_confirmation=False,
        )

    # Build message list: history BEFORE the tool_calls msg (cleaned),
    # then the tool_calls msg itself (manually, so it keeps tool_calls).
    # history_before может кончаться «фото»-user-сообщением — превращаем его
    # обратно в multipart с data-URI, чтобы модель видела картинки во всех
    # раундах лупа (в БД лежит только текстовый факт). Путь только fallback:
    # при успешной экстракции фото в ход не идёт вовсе (turn_images=None).
    history_before = conversation.messages[:pending_idx]
    rebuilt_history = []
    for msg in _db_messages_to_openai(history_before, conversation.summary):
        if (
            images
            and msg.get("role") == "user"
            and isinstance(msg.get("content"), str)
            and "[Клиент прислал фото" in msg["content"]
        ):
            rebuilt_history.append(
                {"role": "user", "content": _user_content(msg["content"], images)}
            )
            images = None  # подставили один раз — дальше текстовый факт
        else:
            rebuilt_history.append(msg)

    system_prompt = build_prompt(business)
    if inspect.isawaitable(system_prompt):
        system_prompt = await system_prompt
    openai_messages = [
        {"role": "system", "content": system_prompt},
        *rebuilt_history,
        {
            "role": "assistant",
            "content": pending_msg.content,
            "tool_calls": tool_calls_data,
        },
    ]

    # Batch execute initial tool calls and collect results
    tool_results = []
    for tc in tool_calls_data:
        try:
            args = json.loads(tc["function"]["arguments"])
            if not isinstance(args, dict):
                raise ValueError("arguments is not an object")
        except (ValueError, TypeError, KeyError):
            # Модель прислала битый JSON аргументов — отдаём модели ошибку
            # вместо падения: она увидит tool_result и попробует ещё раз
            logger.warning("Broken tool arguments in call %r", tc.get("id"))
            args = {}
            tool_result = json.dumps(
                {"error": "Модель прислала некорректные аргументы инструмента"}
            )
        else:
            if on_event is not None:
                # Первый раунд вне лупа — эмитим сами, иначе первый вызов невидим
                await on_event(
                    {
                        "type": "tool_call",
                        "name": tc["function"]["name"],
                        "sql": args.get("sql_query"),
                        "args": {k: v for k, v in args.items() if k != "sql_query"},
                    }
                )
            tool_result = await _execute_tool_call(
                business.id, tc["function"]["name"], args, tool_registry
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
        return await _execute_tool_call(business.id, name, arguments, tool_registry)

    result = await run_openai_analytics_loop(
        client,
        openai_messages,
        execute_tool=_tool,
        model=assistant_model(),
        tools=tools,
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
    user_message: str,
    conversation_id: int | None = None,
    confirmed: bool = False,  # noqa: ARG001 — kept for API compatibility
    *,
    user_id: int | None = None,
    client_id: int | None = None,
    tools: list[dict] = AGENT_TOOLS,
    tool_registry: dict[str, Callable] = TOOL_REGISTRY,
    build_prompt: Callable[[Business], str] = build_system_prompt,
    use_data_question_nudge: bool = True,
    on_event: AIEvHandler = None,
    images: list[ChatImage] | None = None,
) -> ChatResult:
    """
    Main entry point — generic over the tool set/system prompt so both the owner
    analytics agent and other agents (e.g. client booking) can reuse the same loop.
    Tool calls execute immediately — no confirmation flow at the loop level (the
    booking profile enforces confirmation via its own system prompt instead).

    images — фото от пользователя (эскизы, референсы). Сначала отдельный
    мультимодальный вызов снимает с них текстовый анализ; основной ход идёт
    чисто текстом (ноль image-токенов даже в раундах тулов), в историю
    остаётся факт + анализ. Экстракция не удалась — фото уходит модели
    напрямую, как раньше (fallback).
    """
    client = get_openai_client()
    conversation = await _load_or_create_conversation(
        session, business.id, conversation_id, user_id=user_id, client_id=client_id
    )

    # Reset any stale awaiting state
    if conversation.awaiting_confirmation:
        conversation.awaiting_confirmation = False

    # Экстракция: фото → текст. Не удалось — картинки пойдут в модель напрямую
    turn_images = images
    photo_analysis: str | None = None
    if images:
        photo_analysis = await describe_image(images)
        if photo_analysis:
            turn_images = None

    # В историю — только текстовый факт фото + анализ (байты в БД не складываем);
    # сами картинки попадают в openai_messages лишь при fallback, на текущий ход
    stored_text = user_message
    if images:
        photos_part = f"[Клиент прислал фото: {len(images)}]" if len(images) > 1 else (
            "[Клиент прислал фото]"
        )
        stored_text = f"{photos_part} {user_message}".strip()
        if photo_analysis:
            stored_text += f"\n[Анализ фото: {photo_analysis}]"
    user_msg = AIMessage(
        conversation_id=conversation.id,
        role="user",
        content=stored_text,
    )
    session.add(user_msg)
    conversation.messages.append(user_msg)

    system_prompt = build_prompt(business)
    if inspect.isawaitable(system_prompt):
        system_prompt = await system_prompt
    openai_messages = [
        {"role": "system", "content": system_prompt},
        *_db_messages_to_openai(conversation.messages[:-1], conversation.summary),
        # При экстракции — stored_text (факт + анализ), иначе агент хода
        # анализа не увидит; fallback — как раньше: текст + картинки
        {"role": "user", "content": _user_content(
            stored_text if photo_analysis else user_message, turn_images
        )},
    ]

    response = await chat_completion_with_retry(
        client,
        model=assistant_model(),
        temperature=0,
        messages=openai_messages,
        tools=tools,
    )
    choice = response.choices[0]

    if (
        use_data_question_nudge
        and not (choice.finish_reason == "tool_calls" or choice.message.tool_calls)
        and looks_like_data_question(user_message)
    ):
        logger.warning(
            "LLM ответила без вызова execute_analytics_sql на похожий на данные вопрос — "
            "повтор с tool_choice=required | сообщение=%r",
            user_message[:200],
        )
        # Retry with stronger tool requirement but avoid full duplicate context
        response = await chat_completion_with_retry(
            client,
            model=assistant_model(),
            temperature=0,
            messages=[
                *openai_messages,
                {"role": "system", "content": DATA_QUESTION_REQUIRES_TOOL_NUDGE},
            ],
            tools=tools,
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
            session,
            client,
            conversation,
            business,
            tools=tools,
            tool_registry=tool_registry,
            build_prompt=build_prompt,
            on_event=on_event,
            images=turn_images,
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
