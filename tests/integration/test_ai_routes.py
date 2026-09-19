"""AI admin routes: auth and validation without calling a real LLM."""

import pytest


@pytest.fixture
def disable_debug_auth_stub(monkeypatch):
    """Иначе get_current_user в DEBUG подпустит юзера из ALLOWED_USERS без токена."""
    from tg_studio.config import settings

    monkeypatch.setattr(settings, "debug", False)


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


