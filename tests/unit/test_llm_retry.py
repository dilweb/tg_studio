"""Ретраи LLM-вызовов (ai/llm_retry): transient → повтор, остальное — сразу."""

import pytest

from tg_studio.modules.ai import llm_retry


class _Retryable(Exception):
    """Подмена retryable-ошибки openai (конструкторы SDK требуют request/response)."""


class _FakeCompletions:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(outcomes)})()


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(llm_retry, "RETRYABLE_LLM_ERRORS", (_Retryable,))
    monkeypatch.setattr(llm_retry, "LLM_CALL_BACKOFF_SECONDS", 0)


@pytest.mark.asyncio
async def test_retries_retryable_then_succeeds(no_backoff):
    client = _FakeClient([_Retryable("x"), _Retryable("x"), "ok"])
    result = await llm_retry.chat_completion_with_retry(client, model="m")
    assert result == "ok"
    assert client.chat.completions.calls == 3


@pytest.mark.asyncio
async def test_non_retryable_raises_immediately(no_backoff):
    client = _FakeClient([ValueError("bad request")])
    with pytest.raises(ValueError):
        await llm_retry.chat_completion_with_retry(client, model="m")
    assert client.chat.completions.calls == 1


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts(no_backoff):
    client = _FakeClient([_Retryable("x")] * 5)
    with pytest.raises(_Retryable):
        await llm_retry.chat_completion_with_retry(client, model="m")
    assert client.chat.completions.calls == 3


def test_retryable_errors_cover_transient_openai_failures():
    """В tuple только transient-сбои: таймаут, сеть, 429, 5xx провайдера."""
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    for exc in (APITimeoutError, APIConnectionError, InternalServerError, RateLimitError):
        assert exc in llm_retry.RETRYABLE_LLM_ERRORS
