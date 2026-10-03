"""Unit tests for heuristics in openai_agent_loop (no network)."""

import json

import pytest

from tg_studio.modules.ai.openai_agent_loop import (
    looks_like_data_question,
    run_openai_analytics_loop,
)


def _resp(content=None, tool_calls=None):
    return type(
        "Response",
        (),
        {
            "choices": [
                type(
                    "Choice",
                    (),
                    {
                        "message": _FakeMessage(content, tool_calls),
                        "finish_reason": "tool_calls" if tool_calls else "stop",
                    },
                )()
            ]
        },
    )()


class TestRepeatedToolCallGuard:
    async def test_repeat_triggers_nudge_then_second_repeat_stops(self):
        client = _FakeClient(
            [
                _resp(
                    tool_calls=[
                        _FakeToolCall("c1", "get_bookings", '{"date_from": "2026-10-03"}')
                    ]
                ),
                _resp(
                    tool_calls=[
                        _FakeToolCall("c2", "get_bookings", '{"date_from": "2026-10-03"}')
                    ]
                ),
                _resp(
                    tool_calls=[
                        _FakeToolCall("c3", "get_bookings", '{"date_from": "2026-10-03"}')
                    ]
                ),
            ]
        )
        events = []

        async def on_event(ev):
            events.append(ev)

        async def execute_tool(name, args):
            return '{"booking_count": 0}'

        result = await run_openai_analytics_loop(
            client, [{"role": "user", "content": "в субботу в час можно"}],
            execute_tool=execute_tool, tools=None, on_event=on_event,
        )
        kinds = [ev["kind"] for ev in events if ev["type"] == "nudge"]
        assert kinds == ["repeated_tool_call"]
        assert result.stopped_at_max_rounds is True

    async def test_distinct_args_do_not_trigger(self):
        client = _FakeClient(
            [
                _resp(tool_calls=[_FakeToolCall("c1", "get_bookings", '{"date_from": "2026-10-03"}')]),
                _resp(tool_calls=[_FakeToolCall("c2", "get_bookings", '{"date_from": "2026-10-04"}')]),
                _resp(content="Готово."),
            ]
        )
        events = []

        async def on_event(ev):
            events.append(ev)

        async def execute_tool(name, args):
            return "{}"

        result = await run_openai_analytics_loop(
            client, [],
            execute_tool=execute_tool, tools=None, on_event=on_event,
        )
        assert not [ev for ev in events if ev["type"] == "nudge" and ev["kind"] == "repeated_tool_call"]
        assert result.final_reply == "Готово."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("сколько было записей за неделю?", True),
        ("What is the total revenue?", True),
        ("Как погода?", False),
    ],
)
def test_looks_like_data_question(text: str, expected: bool) -> None:
    assert looks_like_data_question(text) is expected


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    async def create(self, **kwargs):
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(responses)})()


async def test_tool_call_event_carries_args_for_non_sql_tool():
    """Эмит tool_call для не-SQL тула: sql=None, args с фильтрами."""

    def _resp(content=None, tool_calls=None):
        return type(
            "Response",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {
                            "message": _FakeMessage(content, tool_calls),
                            "finish_reason": "tool_calls" if tool_calls else "stop",
                        },
                    )()
                ]
            },
        )()

    client = _FakeClient(
        [
            _resp(
                tool_calls=[
                    _FakeToolCall(
                        "call1", "get_bookings", json.dumps({"master": "Евгений"})
                    )
                ]
            ),
            _resp(content="Свободное время есть после 15:00."),
        ]
    )
    events = []

    async def on_event(ev):
        events.append(ev)

    async def execute_tool(name, args):
        return "{}"

    messages = [
        {"role": "user", "content": "какие свободные слоты у Евгения на субботу?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call0",
                    "type": "function",
                    "function": {
                        "name": "execute_analytics_sql",
                        "arguments": json.dumps({"sql_query": "SELECT 1"}),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call0", "content": "{}"},
    ]

    result = await run_openai_analytics_loop(
        client, messages, execute_tool=execute_tool, tools=None, on_event=on_event
    )

    tool_call_events = [ev for ev in events if ev["type"] == "tool_call"]
    assert tool_call_events[-1] == {
        "type": "tool_call",
        "name": "get_bookings",
        "sql": None,
        "args": {"master": "Евгений"},
    }
    assert result.final_reply == "Свободное время есть после 15:00."

