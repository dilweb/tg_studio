"""Integration tests: мягкое удаление мастера (запись остаётся в БД)."""

from tests.conftest import make_business, make_master

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}


async def test_delete_master_is_soft(api_client, db_session):
    """DELETE не удаляет строку: мастер скрыт из списка, но остался в БД
    (works/payments привязывать нечему было бы), и возвращается restore'ом."""
    business = await make_business(db_session, owner_telegram_id=99999)
    master = await make_master(db_session, business_id=business.id, full_name="Иван")
    await db_session.commit()

    response = await api_client.delete(f"/api/admin/masters/{master.id}", headers=OWNER_HEADERS)
    assert response.status_code == 204

    # Скрыт из списка по умолчанию…
    listed = await api_client.get("/api/admin/masters", headers=OWNER_HEADERS)
    assert listed.json() == []

    # …и в публичном (клиентам) тоже
    public = await api_client.get("/api/public/masters")
    assert public.json() == []

    # …но есть с include_deleted и в БД
    with_deleted = await api_client.get(
        "/api/admin/masters", params={"include_deleted": "true"}, headers=OWNER_HEADERS
    )
    row = with_deleted.json()[0]
    assert row["full_name"] == "Иван"
    assert row["deleted_at"] is not None
    assert row["is_active"] is False

    await db_session.refresh(master)
    assert master.deleted_at is not None


async def test_restore_deleted_master(api_client, db_session):
    business = await make_business(db_session, owner_telegram_id=99999)
    master = await make_master(db_session, business_id=business.id, full_name="Иван")
    await db_session.commit()

    await api_client.delete(f"/api/admin/masters/{master.id}", headers=OWNER_HEADERS)

    response = await api_client.post(
        f"/api/admin/masters/{master.id}/restore", headers=OWNER_HEADERS
    )
    assert response.status_code == 200
    assert response.json()["deleted_at"] is None
    assert response.json()["is_active"] is True

    listed = await api_client.get("/api/admin/masters", headers=OWNER_HEADERS)
    assert [m["full_name"] for m in listed.json()] == ["Иван"]
