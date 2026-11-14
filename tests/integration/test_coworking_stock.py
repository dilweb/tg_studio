"""Интеграционные тесты коворкинга 06: склад и продажи.

Покрытие docs/coworking/06-stock-sales.md:
- CRUD товаров, цены, пороги; мастер — read-only;
- закупка: остаток растёт, средневзвешенная себестоимость;
- обеспечение площадки (consume) без оплаты, причина обязательна, привязка к брони;
- ревизия (adjust) с основанием;
- продажа мастеру с баланса: атомарно остаток + charge + апсейл админа;
- продажа разовому контакту за наличные: manual-платёж, апсейл;
- 409 при нехватке остатка или средств — без частичных изменений;
- мастер покупает себе с баланса (403 на чужой master_id / cash);
- возврат: товар на склад + refund на баланс, статус → возвращена (полный);
- продажа админа видна в /api/upsells с sale_id.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from tg_studio.db.models import (
    LedgerEntry,
    LedgerEntryKind,
    Payment,
    SaleStatus,
    Supply,
    SupplyMovement,
    SupplyMovementKind,
    Upsell,
    UserRole,
)

from ..conftest import make_business, make_init_data_headers, make_master, make_user

OWNER_HEADERS = make_init_data_headers(99999)
MASTER_TG_ID = 88888
MASTER_HEADERS = make_init_data_headers(MASTER_TG_ID)
ADMIN_TG_ID = 66666
ADMIN_HEADERS = make_init_data_headers(ADMIN_TG_ID)


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def master(db_session, business):
    user = await make_user(
        db_session, telegram_id=MASTER_TG_ID, first_name="Мастер Тест", role=UserRole.master
    )
    m = await make_master(
        db_session, business_id=business.id, full_name="Мастер Тест", telegram_id=MASTER_TG_ID
    )
    m.user_id = user.id
    await db_session.commit()
    return m


@pytest.fixture
async def admin(db_session, business):
    from tg_studio.db.models import StudioAdmin

    user = await make_user(
        db_session, telegram_id=ADMIN_TG_ID, first_name="Админ Тест", role=UserRole.admin
    )
    a = StudioAdmin(
        business_id=business.id, user_id=user.id, telegram_id=ADMIN_TG_ID, full_name="Админ Тест"
    )
    db_session.add(a)
    await db_session.commit()
    return a


async def _product(api_client, name="Перчатки", qty=100, sale_price=500, purchase_price=200, min_qty=None):
    resp = await api_client.post(
        "/api/products",
        json={
            "name": name,
            "category": "Расходники",
            "unit": "шт",
            "quantity": qty,
            "sale_price": sale_price,
            "purchase_price": purchase_price,
            "min_quantity": min_qty,
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _topup(api_client, master_id, amount=100000):
    resp = await api_client.post(
        f"/api/masters/{master_id}/topup", json={"amount": amount}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Товары и движения
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_products_crud_and_roles(api_client, db_session, business, master, admin):
    p = await _product(api_client)
    assert p["sale_price"] == 500
    assert p["quantity"] == 100

    # Мастер склад не видит вовсе
    assert (await api_client.get("/api/products", headers=MASTER_HEADERS)).status_code == 403
    assert (await api_client.post("/api/products", json={"name": "x", "category": "c", "unit": "шт"}, headers=MASTER_HEADERS)).status_code == 403
    assert (await api_client.patch(f"/api/products/{p['id']}", json={"sale_price": 1}, headers=MASTER_HEADERS)).status_code == 403

    # Админ правит карточку, но остаток — только движениями
    r = await api_client.patch(f"/api/products/{p['id']}", json={"sale_price": 600}, headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json()["sale_price"] == 600

    # Мягкое удаление
    assert (await api_client.delete(f"/api/products/{p['id']}", headers=ADMIN_HEADERS)).status_code == 204
    r = await api_client.get("/api/products", headers=OWNER_HEADERS)
    assert r.json()["products"] == []
    r = await api_client.get("/api/products?include_inactive=true", headers=OWNER_HEADERS)
    assert len(r.json()["products"]) == 1


@pytest.mark.asyncio
async def test_purchase_weighted_average_cost(api_client, db_session, business, admin):
    p = await _product(api_client, qty=10, purchase_price=100)
    # Закупка 10 шт по 300 → средняя (10*100 + 10*300) / 20 = 200
    resp = await api_client.post(
        "/api/stock/purchase", json={"supply_id": p["id"], "qty": 10, "unit_cost": 300}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["quantity"] == 20
    assert resp.json()["purchase_price"] == 200

    movements = (
        await db_session.execute(select(SupplyMovement).where(SupplyMovement.supply_id == p["id"]))
    ).scalars().all()
    kinds = [m.kind for m in movements]
    assert SupplyMovementKind.purchase.value in kinds
    purchase = next(m for m in movements if m.kind == SupplyMovementKind.purchase.value)
    assert purchase.actor_user_id == admin.user_id


@pytest.mark.asyncio
async def test_consume_and_adjust(api_client, db_session, business, admin):
    p = await _product(api_client, qty=50)
    # Обеспечение площадки
    resp = await api_client.post(
        "/api/stock/consume", json={"supply_id": p["id"], "qty": 5, "reason": "Уборка"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["quantity"] == 45
    # Причина обязательна
    resp = await api_client.post(
        "/api/stock/consume", json={"supply_id": p["id"], "qty": 1, "reason": " "}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 422
    # Мастер не списывает через coworking stock (только покупка)
    resp = await api_client.post(
        "/api/stock/consume", json={"supply_id": p["id"], "qty": 1, "reason": "x"}, headers=MASTER_HEADERS
    )
    assert resp.status_code == 403

    # В минус нельзя
    resp = await api_client.post(
        "/api/stock/consume", json={"supply_id": p["id"], "qty": 1000, "reason": "x"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 409

    # Ревизия
    resp = await api_client.post(
        "/api/stock/adjust", json={"supply_id": p["id"], "new_quantity": 40, "reason": "Инвентаризация"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["quantity"] == 40
    movements = (
        await api_client.get(f"/api/stock/movements?supply_id={p['id']}", headers=ADMIN_HEADERS)
    ).json()["movements"]
    assert any(m["kind"] == SupplyMovementKind.adjust.value for m in movements)


@pytest.mark.asyncio
async def test_stock_denormalized_quantity_matches_movements(api_client, db_session, business, admin):
    p = await _product(api_client, qty=10, sale_price=1000)
    await api_client.post(
        "/api/stock/purchase", json={"supply_id": p["id"], "qty": 5, "unit_cost": 250}, headers=ADMIN_HEADERS
    )
    await api_client.post(
        "/api/stock/consume", json={"supply_id": p["id"], "qty": 3, "reason": "Площадка"}, headers=ADMIN_HEADERS
    )
    resp = await api_client.post(
        "/api/sales",
        json={"items": [{"supply_id": p["id"], "qty": 2}], "buyer_name": "Гость", "payment_method": "cash"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201
    await api_client.post(
        f"/api/sales/{resp.json()['id']}/refund",
        json={"items": [{"supply_id": p["id"], "qty": 1}], "reason": "Возврат"},
        headers=ADMIN_HEADERS,
    )
    await api_client.post(
        "/api/stock/adjust", json={"supply_id": p["id"], "new_quantity": 8, "reason": "Инвентаризация"}, headers=ADMIN_HEADERS
    )
    supply = (await db_session.execute(select(Supply).where(Supply.id == p["id"]))).scalar_one()
    movement_sum = (
        await db_session.execute(select(func.sum(SupplyMovement.delta)).where(SupplyMovement.supply_id == p["id"]))
    ).scalar_one()
    assert float(supply.quantity) == float(movement_sum) == 8


@pytest.mark.asyncio
async def test_sale_low_stock_notification_and_buyer_validation(api_client, db_session, business, master, admin, monkeypatch):
    p = await _product(api_client, qty=3, min_qty=2)
    sent = []

    async def fake_notify(_session, supply):
        sent.append((supply.name, float(supply.quantity)))

    monkeypatch.setattr("tg_studio.modules.coworking.stock.notify_low_stock", fake_notify)
    await _topup(api_client, master.id, amount=10000)
    resp = await api_client.post(
        "/api/sales",
        json={"items": [{"supply_id": p["id"], "qty": 2}], "master_id": master.id, "payment_method": "balance"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201
    assert sent == [("Перчатки", 1)]

    resp = await api_client.post(
        "/api/sales",
        json={"items": [{"supply_id": p["id"], "qty": 1}], "payment_method": "balance"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422
    supply = (await db_session.execute(select(Supply).where(Supply.id == p["id"]))).scalar_one()
    assert float(supply.quantity) == 1


# ---------------------------------------------------------------------------
# Продажи
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_to_master_from_balance_atomic(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=10, sale_price=1000, purchase_price=400)
    await _topup(api_client, master.id, amount=5000)

    resp = await api_client.post(
        "/api/sales",
        json={
            "items": [{"supply_id": p["id"], "qty": 2}],
            "master_id": master.id,
            "payment_method": "balance",
            "note": "Расходники мастеру",
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()
    assert sale["total_amount"] == 2000
    assert sale["status"] == SaleStatus.paid.value
    assert sale["admin_id"] == admin.id
    assert sale["items"][0]["unit_price"] == 1000
    assert sale["items"][0]["unit_cost"] == 400  # себестоимость зафиксирована

    # Остаток списан
    supply = (await db_session.execute(select(Supply).where(Supply.id == p["id"]))).scalar_one()
    assert float(supply.quantity) == 8

    # Charge с баланса
    entry = (
        await db_session.execute(
            select(LedgerEntry).where(LedgerEntry.sale_id == sale["id"], LedgerEntry.kind == LedgerEntryKind.charge.value)
        )
    ).scalar_one()
    assert float(entry.amount) == -2000

    # Апсейл админа с sale_id
    upsell = (
        await db_session.execute(select(Upsell).where(Upsell.sale_id == sale["id"]))
    ).scalar_one()
    assert upsell.admin_id == admin.id
    assert float(upsell.amount) == 2000

    # Виден в /api/upsells
    r = await api_client.get("/api/upsells", headers=ADMIN_HEADERS)
    assert any(u["sale_id"] == sale["id"] for u in r.json()["upsells"])


@pytest.mark.asyncio
async def test_sale_insufficient_stock_no_partial_changes(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=1, sale_price=1000)
    await _topup(api_client, master.id, amount=100000)

    resp = await api_client.post(
        "/api/sales",
        json={"items": [{"supply_id": p["id"], "qty": 5}], "master_id": master.id, "payment_method": "balance"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 409

    # Ничего не изменилось: остаток прежний, продаж и проводок нет
    supply = (await db_session.execute(select(Supply).where(Supply.id == p["id"]))).scalar_one()
    assert float(supply.quantity) == 1
    # После rollback fixture-объект master истёк — перечитаем id из БД
    master_id = (
        await db_session.execute(select(type(master).id).where(type(master).telegram_id == MASTER_TG_ID))
    ).scalar_one()
    balance = (await api_client.get(f"/api/masters/{master_id}/balance", headers=OWNER_HEADERS)).json()["balance"]
    assert balance == 100000


@pytest.mark.asyncio
async def test_sale_insufficient_balance(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=100, sale_price=10000)
    await _topup(api_client, master.id, amount=1000)

    resp = await api_client.post(
        "/api/sales",
        json={"items": [{"supply_id": p["id"], "qty": 2}], "master_id": master.id, "payment_method": "balance"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 409
    supply = (await db_session.execute(select(Supply).where(Supply.id == p["id"]))).scalar_one()
    assert float(supply.quantity) == 100  # остаток не тронут


@pytest.mark.asyncio
async def test_sale_to_walkin_cash(api_client, db_session, business, admin):
    p = await _product(api_client, qty=10, sale_price=1500)
    resp = await api_client.post(
        "/api/sales",
        json={
            "items": [{"supply_id": p["id"], "qty": 1}],
            "buyer_name": "Гость",
            "buyer_phone": "+7 777 000 00 00",
            "payment_method": "cash",
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()
    assert sale["buyer_name"] == "Гость"
    assert sale["master_id"] is None

    payment = (
        await db_session.execute(select(Payment).where(Payment.sale_id == sale["id"]))
    ).scalar_one()
    assert payment.status == "paid"
    assert float(payment.amount) == 1500

    # Апсейл без мастера
    upsell = (
        await db_session.execute(select(Upsell).where(Upsell.sale_id == sale["id"]))
    ).scalar_one()
    assert upsell.buyer_name == "Гость"
    assert upsell.master_id is None


@pytest.mark.asyncio
async def test_master_has_no_access_to_stock(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=10, sale_price=500)
    items = {"items": [{"supply_id": p["id"], "qty": 1}], "payment_method": "balance"}
    assert (await api_client.post("/api/sales", json=items, headers=MASTER_HEADERS)).status_code == 403
    for url in ("/api/sales", f"/api/sales/1", "/api/stock/movements"):
        assert (await api_client.get(url, headers=MASTER_HEADERS)).status_code == 403


@pytest.mark.asyncio
async def test_cash_sale_to_master_is_recorded_in_ledger(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=10, sale_price=500)
    resp = await api_client.post(
        "/api/sales",
        json={"items": [{"supply_id": p["id"], "qty": 2}], "master_id": master.id, "payment_method": "cash"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()
    entries = (
        await db_session.execute(select(LedgerEntry).where(LedgerEntry.master_id == master.id))
    ).scalars().all()
    assert sorted((e.kind, float(e.amount)) for e in entries) == [("charge", -1000), ("topup", 1000)]
    assert (await api_client.get(f"/api/masters/{master.id}/balance", headers=OWNER_HEADERS)).json()["balance"] == 0
    # возврат — деньги на баланс мастера
    r = await api_client.post(f"/api/sales/{sale['id']}/refund", json={"reason": "x"}, headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    assert (await api_client.get(f"/api/masters/{master.id}/balance", headers=OWNER_HEADERS)).json()["balance"] == 1000


@pytest.mark.asyncio
async def test_sale_validation_booking_phone_zero_price(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=10, sale_price=500)
    master_id = master.id  # после rollback fixture истекает
    base = {"items": [{"supply_id": p["id"], "qty": 1}], "payment_method": "cash", "buyer_name": "Гость"}
    r = await api_client.post("/api/sales", json={**base, "booking_id": 99999}, headers=ADMIN_HEADERS)
    assert r.status_code == 404
    r = await api_client.post("/api/sales", json={**base, "buyer_phone": "123"}, headers=ADMIN_HEADERS)
    assert r.status_code == 422
    r = await api_client.post("/api/sales", json={**base, "master_id": master_id}, headers=ADMIN_HEADERS)
    assert r.status_code == 422
    r = await api_client.post(
        "/api/products",
        json={"name": "x", "category": "c", "unit": "шт", "sale_price": 0},
        headers=OWNER_HEADERS,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_purchase_without_prior_cost_and_consume_zero(api_client, db_session, business, admin):
    r = await api_client.post(
        "/api/products", json={"name": "Без цены", "category": "c", "unit": "шт", "quantity": 10}, headers=OWNER_HEADERS
    )
    pid = r.json()["id"]
    r = await api_client.post(
        "/api/stock/purchase", json={"supply_id": pid, "qty": 10, "unit_cost": 100}, headers=ADMIN_HEADERS
    )
    assert r.json()["purchase_price"] == 100
    r = await api_client.post(
        "/api/stock/consume", json={"supply_id": pid, "qty": 0.001, "reason": "x"}, headers=ADMIN_HEADERS
    )
    assert r.status_code == 400
    r = await api_client.get("/api/stock/movements?from=bad", headers=ADMIN_HEADERS)
    assert r.status_code == 422
    r = await api_client.get("/api/stock/movements?limit=1", headers=ADMIN_HEADERS)
    assert len(r.json()["movements"]) == 1 and r.json()["total"] == 2


@pytest.mark.asyncio
async def test_refund_rest_after_partial_and_upsell_reduced(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=10, sale_price=1000)
    await _topup(api_client, master.id, amount=10000)
    sale = (
        await api_client.post(
            "/api/sales",
            json={"items": [{"supply_id": p["id"], "qty": 4}], "master_id": master.id, "payment_method": "balance"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await api_client.post(
        f"/api/sales/{sale['id']}/refund",
        json={"items": [{"supply_id": p["id"], "qty": 1}], "reason": "одна"},
        headers=ADMIN_HEADERS,
    )
    upsell = (await db_session.execute(select(Upsell).where(Upsell.sale_id == sale["id"]))).scalar_one()
    await db_session.refresh(upsell)
    assert float(upsell.amount) == 3000
    r = await api_client.post(f"/api/sales/{sale['id']}/refund", json={"reason": "остальное"}, headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == SaleStatus.refunded.value
    await db_session.refresh(upsell)
    assert float(upsell.amount) == 0


# ---------------------------------------------------------------------------
# Возвраты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_full_returns_stock_and_money(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=10, sale_price=1000)
    await _topup(api_client, master.id, amount=10000)
    resp = await api_client.post(
        "/api/sales",
        json={"items": [{"supply_id": p["id"], "qty": 2}], "master_id": master.id, "payment_method": "balance"},
        headers=ADMIN_HEADERS,
    )
    sale = resp.json()
    balance_after_sale = (await api_client.get(f"/api/masters/{master.id}/balance", headers=OWNER_HEADERS)).json()["balance"]
    assert balance_after_sale == 8000

    resp = await api_client.post(
        f"/api/sales/{sale['id']}/refund", json={"reason": "Брак"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == SaleStatus.refunded.value

    supply = (await db_session.execute(select(Supply).where(Supply.id == p["id"]))).scalar_one()
    assert float(supply.quantity) == 10  # товар вернулся

    refund_entry = (
        await db_session.execute(
            select(LedgerEntry).where(LedgerEntry.sale_id == sale["id"], LedgerEntry.kind == LedgerEntryKind.refund.value)
        )
    ).scalar_one()
    assert float(refund_entry.amount) == 2000
    balance = (await api_client.get(f"/api/masters/{master.id}/balance", headers=OWNER_HEADERS)).json()["balance"]
    assert balance == 10000

    # Повторный полный возврат — 409
    resp = await api_client.post(
        f"/api/sales/{sale['id']}/refund", json={"reason": "ещё раз"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_refund_partial(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=10, sale_price=1000)
    await _topup(api_client, master.id, amount=10000)
    resp = await api_client.post(
        "/api/sales",
        json={"items": [{"supply_id": p["id"], "qty": 4}], "master_id": master.id, "payment_method": "balance"},
        headers=ADMIN_HEADERS,
    )
    sale = resp.json()

    # Частичный возврат 1 шт
    resp = await api_client.post(
        f"/api/sales/{sale['id']}/refund",
        json={"items": [{"supply_id": p["id"], "qty": 1}], "reason": "Одна лишняя"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == SaleStatus.paid.value  # частичный — статус не меняется

    supply = (await db_session.execute(select(Supply).where(Supply.id == p["id"]))).scalar_one()
    assert float(supply.quantity) == 7  # 10 - 4 + 1
    balance = (await api_client.get(f"/api/masters/{master.id}/balance", headers=OWNER_HEADERS)).json()["balance"]
    assert balance == 7000  # 10000 - 4000 + 1000

    # Второй частичный возврат не может превысить оставшиеся 3 шт
    resp = await api_client.post(
        f"/api/sales/{sale['id']}/refund",
        json={"items": [{"supply_id": p["id"], "qty": 3.01}], "reason": "Слишком много"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 409
    supply = (await db_session.execute(select(Supply).where(Supply.id == p["id"]))).scalar_one()
    assert float(supply.quantity) == 7

    # Оставшиеся 3 шт — уже полный возврат по накопленной сумме движений
    resp = await api_client.post(
        f"/api/sales/{sale['id']}/refund",
        json={"items": [{"supply_id": p["id"], "qty": 3}], "reason": "Остальное"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == SaleStatus.refunded.value
    supply = (await db_session.execute(select(Supply).where(Supply.id == p["id"]))).scalar_one()
    assert float(supply.quantity) == 10


@pytest.mark.asyncio
async def test_upsells_split_master_vs_client(api_client, db_session, business, master, admin):
    p = await _product(api_client, qty=20, sale_price=100)
    await _topup(api_client, master.id, amount=10000)
    for body in (
        {"master_id": master.id, "payment_method": "balance", "qty": 3},
        {"buyer_name": "Гость", "payment_method": "cash", "qty": 2},
    ):
        qty = body.pop("qty")
        r = await api_client.post(
            "/api/sales", json={**body, "items": [{"supply_id": p["id"], "qty": qty}]}, headers=ADMIN_HEADERS
        )
        assert r.status_code == 201, r.text
    r = await api_client.get("/api/upsells?buyer_type=master", headers=OWNER_HEADERS)
    assert [(u["buyer_type"], u["amount"]) for u in r.json()["upsells"]] == [("master", 300)]
    r = await api_client.get("/api/upsells?buyer_type=client", headers=OWNER_HEADERS)
    assert [(u["buyer_type"], u["amount"]) for u in r.json()["upsells"]] == [("client", 200)]
    now = datetime.now(UTC)
    r = await api_client.get(
        "/api/reports/upsells",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": (now + timedelta(days=1)).isoformat()},
        headers=OWNER_HEADERS,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert (data["master_amount"], data["client_amount"]) == (300, 200)
