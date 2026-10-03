"""Юнит-тесты клиентского промпта-заглушки для песочницы."""

import asyncio
from datetime import date

from tg_studio.db.models import Business
from tg_studio.modules.ai.system_prompt import (
    CLIENT_SYSTEM_TEMPLATE,
    build_client_prompt,
    build_system_prompt,
)


def test_client_prompt_renders_business_name():
    prompt = asyncio.run(build_client_prompt(Business(name="BNZ Tattoo")))
    assert "BNZ Tattoo" in prompt


def test_client_prompt_differs_from_owner_prompt():
    owner = asyncio.run(build_system_prompt(Business(name="BNZ Tattoo")))
    client = asyncio.run(build_client_prompt(Business(name="BNZ Tattoo")))
    assert client != owner


def test_client_prompt_is_static():
    """Без интроспекции схемы: рендер зависит только от скалярных подстановок."""
    prompt = asyncio.run(build_client_prompt(Business(name="X")))
    expected = CLIENT_SYSTEM_TEMPLATE.format(
        business_name="X",
        today=date.today().isoformat(),
        masters="(the get_bookings tool will list them)",  # БД недоступна → фолбэк
    )
    assert prompt == expected


def test_client_prompt_teaches_both_tools():
    prompt = asyncio.run(build_client_prompt(Business(name="BNZ")))
    assert "get_bookings" in prompt
    assert "execute_analytics_sql" in prompt


def test_owner_prompt_teaches_bookings_tool():
    prompt = asyncio.run(build_system_prompt(Business(name="BNZ")))
    assert "get_bookings" in prompt
    # структурированные ошибки тула, по которым модель самоисправляется
    assert "master_not_found" in prompt
