"""Integration tests for the public bookings endpoints."""

from tg_studio.modules.booking.registry import SERVICE_TYPES


async def test_service_types_returns_registry(api_client):
    response = await api_client.get("/api/bookings/service-types")
    assert response.status_code == 200
    assert response.json() == SERVICE_TYPES
