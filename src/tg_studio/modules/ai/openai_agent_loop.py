"""
Agentic loop for OpenAI Chat Completions + tools (analog to documind’s run_agent, but OpenAI format).

Drives a multi-turn tool loop: completion → if tool_calls, execute → append tool messages →
repeat until the model returns normal text (or cap). Optional zero-aggregate nudge reuses
system_prompt.ZERO_COUNT_FOLLOWUP_NUDGE in-memory (not stored in the DB).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageToolCall

from tg_studio.config import settings

from .system_prompt import ZERO_COUNT_FOLLOWUP_NUDGE
from .tools import ANALYTICS_TOOLS

logger = logging.getLogger(__name__)

# --- Event callback for SSE / progress (JSON-serializable dict) ---
type AgentEventHandler = Callable[[dict[str, Any]], Awaitable[None]] | None

MAX_TOOL_ROUNDS_DEFAULT = 12
MAX_ZERO_RESULT_NUDGES = 2

_DATA_Q = re.compile(
    r"сколько|скольки|запис|бронир|выруч|заработ|доход|клиент|мастер|продюс|"
    r"услуг|статистик|аналитик|отмен|число|колич|рейтинг|"
    r"how\s+many|revenue|earnings|count\b",
    re.IGNORECASE,
)


@dataclass
class PendingAIMessage:
    """Rows to store as tg_studio.db.models.AIMessage (same as client previously built inline)."""

    role: str
    content: str | None
    tool_calls_json: str | None = None
    tool_call_id: str | None = None


@dataclass
class OpenAIAgentLoopResult:
    """
    Result of the tool loop. `pending_db_messages` are rows to persist; in-memory-only
    system nudges (e.g. zero-count follow-up) are not included.
    """

    final_reply: str
    pending_db_messages: list[PendingAIMessage]
    stopped_at_max_rounds: bool = False


def _serialize_tool_calls(tool_calls: list[ChatCompletionMessageToolCall]) -> str:
    return json.dumps(
        [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ],
        ensure_ascii=False,
    )


def _tool_result_looks_like_row_directory_not_aggregate(data: dict) -> bool:
    cols = {str(c).lower() for c in (data.get("columns") or [])}
    if any(
        "count" in c or c in ("cnt", "total", "sum", "avg", "min", "max")
        for c in cols
    ):
        return False
    if "id" not in cols:
        return False
    return any(c in cols for c in ("full_name", "name", "title", "label"))


def _last_tool_needs_zero_count_followup(messages: list[dict]) -> bool:
    last_tool: dict | None = None
    for m in reversed(messages):
        if m.get("role") == "tool":
            last_tool = m
            break
    if not last_tool:
        return False
    try:
        data = json.loads(last_tool["content"])
    except json.JSONDecodeError:
        return False
    if data.get("error"):
        return False
    if _tool_result_looks_like_row_directory_not_aggregate(data):
        return False
    rows = data.get("rows") or []
    if len(rows) != 1:
        return False
    row = rows[0]
    if not isinstance(row, dict):
        return False
    for k, v in row.items():
        lk = str(k).lower()
        if ("count" in lk or lk in ("cnt", "total")) and v in (0, None, "0"):
            return True
    return False


def _replace_or_append_nudge(messages: list[dict], nudge_content: str) -> None:
    """Replace the last system nudge message if present, otherwise append a new one."""
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "system" and ZERO_COUNT_FOLLOWUP_NUDGE in msg.get("content", ""):
            messages[i]["content"] = nudge_content
            return
    messages.append({"role": "system", "content": nudge_content})


def looks_like_data_question(text: str) -> bool:
    return bool(_DATA_Q.search(text.strip()))


def serialize_openai_tool_calls(
    tool_calls: list[ChatCompletionMessageToolCall],
) -> str:
    """JSON for AIMessage.tool_calls_json (from chat.completions assistant message)."""
    if not tool_calls:
        return "[]"
    return _serialize_tool_calls(tool_calls)


async def run_openai_analytics_loop(
    client: AsyncOpenAI,
    openai_messages: list[dict],
    *,
    execute_tool: Callable[[str, dict], Awaitable[str]],
    model: str | None = None,
    tools: list[dict] | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS_DEFAULT,
    max_zero_nudges: int = MAX_ZERO_RESULT_NUDGES,
    on_event: AgentEventHandler = None,
) -> OpenAIAgentLoopResult:
    """
    From a state where `openai_messages` already includes the user thread + the assistant
    that requested tool_calls + **tool** results for that round, run further completions
    until a non-tool final reply or the round cap.
    """
    mdl = model or settings.llm_model
    tdefs = tools if tools is not None else ANALYTICS_TOOLS
    pending: list[PendingAIMessage] = []
    zero_nudges = 0
    for round_idx in range(max_tool_rounds):
        if on_event is not None:
            await on_event(
                {
                    "type": "turn",
                    "round": round_idx,
                    "phase": "completions",
                }
            )
        response = await client.chat.completions.create(
            model=mdl,
            temperature=0,
            messages=openai_messages,
            tools=tdefs,
        )
        choice = response.choices[0]
        assistant_msg = choice.message
        if assistant_msg is None:
            return OpenAIAgentLoopResult(
                final_reply="Пустой ответ модели.",
                pending_db_messages=pending,
            )
        if choice.finish_reason == "tool_calls" or assistant_msg.tool_calls:
            tcalls = list(assistant_msg.tool_calls or [])
            if not tcalls:
                tr = (assistant_msg.content or "").strip()
                if tr:
                    pending.append(
                        PendingAIMessage(role="assistant", content=tr)
                    )
                    return OpenAIAgentLoopResult(
                        final_reply=tr,
                        pending_db_messages=pending,
                    )
                continue
            tjson = _serialize_tool_calls(tcalls)
            pending.append(
                PendingAIMessage(
                    role="assistant",
                    content=assistant_msg.content,
                    tool_calls_json=tjson,
                )
            )
            openai_messages.append(
                {
                    "role": "assistant",
                    "content": assistant_msg.content,
                    "tool_calls": json.loads(tjson),
                }
            )
            for tc in tcalls:
                if on_event is not None:
                    await on_event(
                        {
                            "type": "tool_call",
                            "name": tc.function.name,
                        }
                    )
                args = json.loads(tc.function.arguments)
                tool_result = await execute_tool(tc.function.name, args)
                pending.append(
                    PendingAIMessage(
                        role="tool",
                        content=tool_result,
                        tool_call_id=tc.id,
                    )
                )
                openai_messages.append(
                    {
                        "role": "tool",
                        "content": tool_result,
                        "tool_call_id": tc.id,
                    }
                )
            continue
        if (
            zero_nudges < max_zero_nudges
            and _last_tool_needs_zero_count_followup(openai_messages)
        ):
            logger.info(
                "Zero aggregate from tool — nudge model to run follow-up SQL (nudge %s/%s)",
                zero_nudges + 1,
                max_zero_nudges,
            )
            if on_event is not None:
                await on_event({"type": "nudge", "kind": "zero_count_followup"})
            # Replace existing nudge message instead of accumulating multiple system messages
            _replace_or_append_nudge(openai_messages, ZERO_COUNT_FOLLOWUP_NUDGE)
            zero_nudges += 1
            continue
        reply_text = (assistant_msg.content or "").strip()
        pending.append(PendingAIMessage(role="assistant", content=reply_text or None))
        return OpenAIAgentLoopResult(
            final_reply=reply_text
            or "Нет ответа.",
            pending_db_messages=pending,
        )

    return OpenAIAgentLoopResult(
        final_reply="",
        pending_db_messages=pending,
        stopped_at_max_rounds=True,
    )
