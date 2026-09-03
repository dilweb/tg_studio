"""AI admin routes: auth and validation without calling a real LLM."""


class TestAIChatRoutesAuth:
    async def test_post_json_requires_auth(self, api_client) -> None:
        response = await api_client.post(
            "/api/admin/ai-chat",
            json={"message": "test"},
        )
        assert response.status_code == 401

    async def test_post_stream_requires_auth(self, api_client) -> None:
        response = await api_client.post(
            "/api/admin/ai-chat/stream",
            json={"message": "test"},
        )
        assert response.status_code == 401


