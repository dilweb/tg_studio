"""Unit tests for heuristics in openai_agent_loop (no network)."""

import pytest

from tg_studio.modules.ai.openai_agent_loop import looks_like_data_question


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
