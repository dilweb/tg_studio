"""AI admin routes: auth and validation without calling a real LLM."""

import pytest

from tg_studio.modules.ai import api as ai_api
from tg_studio.modules.ai.client import ChatResult
from tg_studio.modules.ai.system_prompt import build_client_prompt, build_system_prompt

from ..conftest import make_business, make_client

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}


@pytest.fixture
def disable_debug_auth_stub(monkeypatch):
    """Иначе get_current_user в DEBUG подпустит юзера из ALLOWED_USERS без токена."""
    from tg_studio.config import settings

    monkeypatch.setattr(settings, "debug", False)


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


class TestAIChatRoutesAuth:
    async def test_post_json_requires_auth(self, api_client, disable_debug_auth_stub) -> None:
        response = await api_client.post(
            "/api/admin/ai-chat",
            json={"message": "test"},
        )
        assert response.status_code == 401

    async def test_post_stream_requires_auth(self, api_client, disable_debug_auth_stub) -> None:
        response = await api_client.post(
            "/api/admin/ai-chat/stream",
            json={"message": "test"},
        )
        assert response.status_code == 401

    async def test_list_clients_requires_auth(self, api_client, disable_debug_auth_stub) -> None:
        response = await api_client.get("/api/admin/ai-chat/clients")
        assert response.status_code == 401


class TestVariantMapping:
    """Вариант решает личность диалога (user_id vs client_id) и промпт."""

    @pytest.mark.asyncio
    async def test_owner_variant_by_default(self, api_client, db_session, business, monkeypatch):
        captured = {}

        async def fake_chat(**kwargs):
            captured.update(kwargs)
            return ChatResult(reply="ок", conversation_id=1, awaiting_confirmation=False)

        monkeypatch.setattr(ai_api, "chat", fake_chat)

        resp = await api_client.post(
            "/api/admin/ai-chat", json={"message": "привет"}, headers=OWNER_HEADERS
        )
        assert resp.status_code == 200
        assert captured["user_id"] is not None
        assert captured["client_id"] is None
        assert captured["build_prompt"] is build_system_prompt

    @pytest.mark.asyncio
    async def test_client_variant_uses_client_identity(
        self, api_client, db_session, business, monkeypatch
    ):
        client = await make_client(db_session, telegram_id=42, full_name="Тест Клиент")
        captured = {}

        async def fake_chat(**kwargs):
            captured.update(kwargs)
            return ChatResult(reply="ок", conversation_id=2, awaiting_confirmation=False)

        monkeypatch.setattr(ai_api, "chat", fake_chat)

        resp = await api_client.post(
            "/api/admin/ai-chat",
            json={"message": "привет", "variant": "client", "client_id": client.id},
            headers=OWNER_HEADERS,
        )
        assert resp.status_code == 200
        assert captured["user_id"] is None
        assert captured["client_id"] == client.id
        assert captured["build_prompt"] is build_client_prompt

    @pytest.mark.asyncio
    async def test_client_variant_requires_client_id(self, api_client, business):
        resp = await api_client.post(
            "/api/admin/ai-chat",
            json={"message": "привет", "variant": "client"},
            headers=OWNER_HEADERS,
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_client_variant_unknown_client(self, api_client, business):
        resp = await api_client.post(
            "/api/admin/ai-chat",
            json={"message": "привет", "variant": "client", "client_id": 424242},
            headers=OWNER_HEADERS,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_variant_rejected(self, api_client, business):
        resp = await api_client.post(
            "/api/admin/ai-chat",
            json={"message": "привет", "variant": "other"},
            headers=OWNER_HEADERS,
        )
        assert resp.status_code == 422


class TestClientsEndpoint:
    @pytest.mark.asyncio
    async def test_lists_clients_sorted(self, api_client, db_session, business):
        await make_client(db_session, telegram_id=101, full_name="Борис")
        await make_client(db_session, telegram_id=102, full_name="Аня")

        resp = await api_client.get("/api/admin/ai-chat/clients", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert [(c["full_name"], c["id"] is not None) for c in resp.json()] == [
            ("Аня", True),
            ("Борис", True),
        ]


class TestSSEHeaders:
    @pytest.mark.asyncio
    async def test_stream_disables_proxy_buffering(self, api_client, business, monkeypatch):
        async def fake_stream(**kwargs):
            yield 'data: {"type": "final"}\n\n'

        monkeypatch.setattr(ai_api, "stream_ai_chat_sse", fake_stream)

        resp = await api_client.post(
            "/api/admin/ai-chat/stream", json={"message": "привет"}, headers=OWNER_HEADERS
        )
        assert resp.status_code == 200
        assert resp.headers["x-accel-buffering"] == "no"
        assert 'data: {"type": "final"}' in resp.text


