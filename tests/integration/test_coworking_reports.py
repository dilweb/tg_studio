"""Интеграционные тесты дашборда, отчётов и Excel (docs/coworking/07)."""

from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from tg_studio.db.models import (
    Booking,
    BookingEvent,
    BookingEventKind,
    BookingStatus,
    LedgerEntry,
    LedgerEntryKind,
    Payment,
    PaymentStatus,
    Sale,
    SeatBlock,
    Supply,
    SupplyMovement,
    SupplyMovementKind,
    UserRole,
)
from tg_studio.timeutil import STUDIO_TZ

from ..conftest import make_business, make_init_data_headers, make_master, make_user

OWNER_HEADERS = make_init_data_headers(99999)
MASTER_TG_ID = 88888
MASTER_HEADERS = make_init_data_headers(MASTER_TG_ID)


def at(day: int, hour: int) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=STUDIO_TZ).astimezone(UTC)


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def master(db_session, business):
    user = await make_user(db_session, telegram_id=MASTER_TG_ID, first_name="Мастер", role=UserRole.master)
    user.created_at = at(3, 10)
    master = await make_master(db_session, business_id=business.id, full_name="Мастер Отчёт")
    master.user_id = user.id
    await db_session.commit()
    await db_session.refresh(user)
    assert user.created_at == at(3, 10)
    return master


@pytest.fixture
async def reporting_facts(api_client, db_session, business, master):
    seats = []
    for index in range(4):
        response = await api_client.post("/api/seats", json={"name": f"Место {index + 1}"}, headers=OWNER_HEADERS)
        assert response.status_code == 201
        seats.append(response.json())
    booking = Booking(
        business_id=business.id, seat_id=seats[0]["id"], master_id=master.id,
        start=at(3, 10), end=at(3, 13), status=BookingStatus.completed.value,
        paid_amount=6000, price_snapshot={"final": 6000},
        created_by_user_id=business.owner_id,
    )
    booking.created_at = at(3, 8)
    cancelled = Booking(
        business_id=business.id, seat_id=seats[1]["id"], master_id=master.id,
        start=at(4, 14), end=at(4, 15), status=BookingStatus.cancelled_by_master.value,
        paid_amount=1000, price_snapshot={"final": 1000}, created_by_user_id=business.owner_id,
    )
    db_session.add_all([booking, cancelled])
    await db_session.flush()
    payment = Payment(
        business_id=business.id, master_id=master.id, booking_id=booking.id,
        amount=6000, kind="prepay", status=PaymentStatus.paid.value,
        provider="apipay", external_invoice_id="bank-report-07",
        created_by_user_id=business.owner_id, paid_at=at(3, 8),
    )
    db_session.add(payment)
    await db_session.flush()
    cancellation_refund = LedgerEntry(business_id=business.id, master_id=master.id, kind=LedgerEntryKind.refund.value, amount=1000, booking_id=cancelled.id)
    cancellation_refund.created_at = at(4, 12)
    db_session.add_all([
        LedgerEntry(business_id=business.id, master_id=master.id, kind=LedgerEntryKind.topup.value, amount=6000, payment_id=payment.id),
        LedgerEntry(business_id=business.id, master_id=master.id, kind=LedgerEntryKind.charge.value, amount=-6000, booking_id=booking.id),
        cancellation_refund,
        BookingEvent(
            business_id=business.id, booking_id=cancelled.id, kind=BookingEventKind.refunded.value,
            details={"amount": 1000, "held": 0}, created_at=at(4, 12),
        ),
    ])
    supply = Supply(business_id=business.id, name="Плёнка", category="Расходники", unit="шт", quantity=10, min_quantity=2, purchase_price=200, sale_price=500)
    db_session.add(supply)
    await db_session.flush()
    sale = Sale(
        business_id=business.id, master_id=master.id, total_amount=2000, payment_method="cash",
        created_by_user_id=business.owner_id,
    )
    from tg_studio.db.models import SaleItem
    sale.items = [SaleItem(supply_id=supply.id, qty=4, unit_price=500, unit_cost=200, amount=2000)]
    sale.created_at = at(3, 12)
    db_session.add(sale)
    await db_session.flush()
    sale_payment = Payment(
        business_id=business.id, master_id=master.id, sale_id=sale.id,
        amount=2000, kind="final", status=PaymentStatus.paid.value,
        provider="manual", created_by_user_id=business.owner_id, paid_at=at(3, 12),
    )
    db_session.add(sale_payment)
    await db_session.flush()
    refund = LedgerEntry(business_id=business.id, master_id=master.id, kind=LedgerEntryKind.refund.value, amount=500, sale_id=sale.id)
    refund.created_at = at(4, 12)
    db_session.add_all([
        refund,
        SupplyMovement(business_id=business.id, supply_id=supply.id, delta=1, kind=SupplyMovementKind.refund.value, sale_id=sale.id, created_at=at(4, 12)),
        BookingEvent(
            business_id=business.id, booking_id=booking.id, kind=BookingEventKind.refunded.value,
            details={"sale_id": sale.id, "amount": 500, "qty_by_supply": {str(supply.id): 1}}, created_at=at(4, 12),
        ),
        SeatBlock(business_id=business.id, seat_id=seats[1]["id"], start=at(3, 10), end=at(3, 12), reason="Уборка", created_by_user_id=business.owner_id),
    ])
    await db_session.commit()
    return {"booking": booking, "sale": sale, "seats": seats, "master": master}


def period(client, path, from_day=3, to_day=5, **kwargs):
    return client.get(path, params={"from": at(from_day, 0).isoformat(), "to": at(to_day, 0).isoformat(), **kwargs}, headers=OWNER_HEADERS)


@pytest.mark.asyncio
async def test_reports_match_facts_and_owner_only(api_client, reporting_facts):
    response = await period(api_client, "/api/reports/revenue")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["booking_revenue"] == 7000
    assert report["sales_revenue"] == 2000
    assert report["gross"] == 9000
    assert report["booking_refunds"] == 1000
    assert report["sale_refunds"] == 500
    assert report["refunds"] == 1500
    assert report["net"] == 7500
    assert sum(day["gross"] for day in report["by_day"]) == report["gross"]
    assert sum(seat["revenue"] for seat in report["by_seat"]) == report["booking_revenue"]
    assert report["by_seat"][0]["hours"] == 3

    response = await period(api_client, "/api/reports/usage")
    assert response.status_code == 200, response.text
    usage = response.json()
    assert usage["available_hours"] == 96
    assert usage["occupied_hours"] == 3
    assert usage["blocked_hours"] == 2
    assert usage["seats"][0]["free_hours"] == 21
    assert usage["seats"][1]["free_hours"] == 22

    response = await period(api_client, "/api/reports/money")
    assert response.status_code == 200, response.text
    money = response.json()
    # Topup с баланса — не второй приход: только фактические paid Payment.
    assert money["receipts"] == 8000
    assert money["refunds"] == 1500
    assert money["net_cash"] == 8000  # оба возврата ушли на баланс мастера, из кассы не выдавались
    # 6000 пополнение - 6000 списание + 1000 и 500 возвратов на баланс.
    assert money["total_balance"] == 1500
    assert len(money["payments"]) == 2
    assert len(money["refund_rows"]) == 2
    assert any(row["external_id"] == "bank-report-07" for row in money["payments"])
    sale_row = next(row for row in money["refund_rows"] if row["sale_id"] == reporting_facts["sale"].id)
    assert sale_row["type"] == "sale"
    assert sale_row["amount"] == 500
    assert sale_row["master_name"] == "Мастер Отчёт"

    response = await period(api_client, "/api/reports/masters")
    assert response.status_code == 200, response.text
    masters = response.json()
    assert masters["new"] == 1
    assert masters["active"] == 1
    assert masters["masters"][0]["hours"] == 3
    assert masters["masters"][0]["revenue"] == 7500  # 9000 минус возвраты 1500
    assert masters["masters"][0]["refunds"] == 1500
    assert masters["masters"][0]["balance"] == 1500

    response = await period(api_client, "/api/reports/upsells")
    assert response.status_code == 200, response.text
    assert response.json()["amount"] == 0

    response = await api_client.get("/api/dashboard", headers=MASTER_HEADERS)
    assert response.status_code == 403
    response = await period(api_client, "/api/reports/revenue.xlsx")
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_excel_contains_operations_refunds_and_cost(api_client, reporting_facts):
    response = await period(api_client, "/api/reports/revenue.xlsx")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    workbook = load_workbook(BytesIO(response.content), data_only=True)
    assert workbook.sheetnames == ["Выручка", "Возвраты и удержания", "Продажи товаров", "Поступления"]
    revenue = workbook["Выручка"]
    rows = list(revenue.iter_rows(min_row=2, values_only=True))
    assert any(row[7] == "bank-report-07" and row[2] == "Мастер Отчёт" and row[3] == 6000 for row in rows)
    assert any(row[1] == "Продажа" and row[3] == 2000 for row in rows)
    assert revenue.cell(revenue.max_row, 4).value == 9000
    refunds = workbook["Возвраты и удержания"]
    assert refunds.cell(refunds.max_row, 4).value == 1500
    products = workbook["Продажи товаров"]
    assert products.cell(2, 4).value == "Плёнка"
    assert products.cell(2, 9).value == 800
    assert products.cell(products.max_row, 7).value == 2000


@pytest.mark.asyncio
async def test_period_validation_and_refund_after_original_sale(api_client, db_session, reporting_facts):
    response = await api_client.get("/api/reports/revenue", params={"from": "2026-11-03T00:00", "to": "2026-11-04T00:00"}, headers=OWNER_HEADERS)
    assert response.status_code == 422
    response = await period(api_client, "/api/reports/revenue", from_day=4, to_day=3)
    assert response.status_code == 422
    # Возврат попал в период, а сама продажа — до него.
    response = await period(api_client, "/api/reports/revenue", from_day=4, to_day=5)
    assert response.status_code == 200, response.text
    assert response.json()["gross"] == 1000
    assert response.json()["refunds"] == 1500


@pytest.mark.asyncio
async def test_dashboard_reconciles_today_and_poll_source(api_client, reporting_facts, monkeypatch):
    from tg_studio.modules.coworking import reports as reports_module

    # Polling и границы «сегодня» используют Asia/Almaty, а не дату сервера.
    monkeypatch.setattr(reports_module, "studio_now", lambda: at(3, 12))
    response = await api_client.get("/api/dashboard", headers=OWNER_HEADERS)
    assert response.status_code == 200, response.text
    dashboard = response.json()
    assert dashboard["from"].startswith("2026-11-03T00:00:00+05:00")
    assert dashboard["to"].startswith("2026-11-04T00:00:00+05:00")
    assert dashboard["revenue"]["gross"] == 8000
    assert dashboard["revenue"]["refunds"] == 0
    assert dashboard["money"]["receipts"] == 8000
    assert dashboard["money"]["total_balance"] == 1500
    assert dashboard["master_balances"] == 1500
    assert dashboard["usage"]["occupied_hours"] == 3
    assert dashboard["usage"]["blocked_hours"] == 2
    assert dashboard["bookings_created"] == 1
    assert dashboard["cancellations"] == 0
    assert len(dashboard["seats_now"]) == 4
    assert all(row["status"] == "free" for row in dashboard["seats_now"])


@pytest.mark.asyncio
async def test_partial_period_split_has_consistent_excel(api_client, reporting_facts):
    bounds = {"from": at(3, 11).isoformat(), "to": at(3, 13).isoformat()}
    response = await period(api_client, "/api/reports/revenue", **bounds)
    assert response.status_code == 200, response.text
    assert response.json()["gross"] == 6000  # 2 часа брони + продажа в 12:00
    assert sum(row["gross"] for row in response.json()["by_day"]) == 6000
    assert sum(row["hours"] for row in response.json()["by_day"]) == 2  # часы, а не секунды/3600 дважды
    response = await period(api_client, "/api/reports/revenue.xlsx", **bounds)
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content), data_only=True)
    sheet = workbook["Выручка"]
    row = next(row for row in sheet.iter_rows(min_row=2, values_only=True) if row[8] == reporting_facts["booking"].id)
    assert row[3] == 4000
    assert row[5] == 4000
    assert sheet.cell(sheet.max_row, 4).value == 6000


@pytest.mark.asyncio
async def test_prior_payment_is_not_lost(api_client, db_session, reporting_facts):
    booking = reporting_facts["booking"]
    # Банковский id сохраняется на строке этой брони в Excel.
    # Деньги пришли до периода выручки: cash не дублируется, но банковский id
    # остаётся доступен бухгалтеру на строке этой брони в Excel.
    payment = (await db_session.execute(
        select(Payment).where(Payment.booking_id == booking.id)
    )).scalar_one()
    payment.paid_at = at(2, 8)
    await db_session.commit()

    response = await period(api_client, "/api/reports/revenue")
    assert response.status_code == 200, response.text
    assert response.json()["booking_revenue"] == 7000
    response = await period(api_client, "/api/reports/money")
    assert response.status_code == 200, response.text
    money = response.json()
    assert money["receipts"] == 2000
    assert len(money["payments"]) == 1
    response = await period(api_client, "/api/reports/revenue.xlsx")
    assert response.status_code == 200, response.text
    sheet = load_workbook(BytesIO(response.content), data_only=True)["Выручка"]
    assert any(row[7] == "bank-report-07" and row[8] == booking.id for row in sheet.iter_rows(min_row=2, values_only=True))


@pytest.mark.asyncio
async def test_dashboard_seats_blocked_upcoming_once_and_inactive_hidden(
    api_client, db_session, reporting_facts, business, monkeypatch
):
    from tg_studio.modules.coworking import reports as reports_module

    seats, master = reporting_facts["seats"], reporting_facts["master"]
    db_session.add(Booking(
        business_id=business.id, seat_id=seats[2]["id"], master_id=master.id,
        start=at(3, 14), end=at(3, 15), status=BookingStatus.confirmed.value,
        paid_amount=0, price_snapshot={"final": 0}, created_by_user_id=business.owner_id,
    ))
    await db_session.commit()
    r = await api_client.patch(f"/api/seats/{seats[3]['id']}", json={"is_active": False}, headers=OWNER_HEADERS)
    assert r.status_code == 200, r.text
    monkeypatch.setattr(reports_module, "studio_now", lambda: at(3, 11))
    rows = {row["seat_id"]: row for row in (await api_client.get("/api/dashboard", headers=OWNER_HEADERS)).json()["seats_now"]}
    assert seats[3]["id"] not in rows
    assert rows[seats[0]["id"]]["status"] == "free"  # бронь завершена
    assert rows[seats[1]["id"]]["status"] == "blocked"
    assert rows[seats[2]["id"]]["status"] == "free"
    assert rows[seats[2]["id"]]["upcoming"] == 1  # раньше бронь считалась дважды


@pytest.mark.asyncio
async def test_usage_clipped_to_working_hours(api_client, db_session, reporting_facts, business):
    seats, master = reporting_facts["seats"], reporting_facts["master"]
    # бронь 21:00–24:00 при рабочем дне до 22:00: занято считается только до 22:00
    db_session.add(Booking(
        business_id=business.id, seat_id=seats[2]["id"], master_id=master.id,
        start=at(3, 21), end=at(3, 21) + timedelta(hours=3), status=BookingStatus.completed.value,
        paid_amount=0, price_snapshot={"final": 0}, created_by_user_id=business.owner_id,
    ))
    await db_session.commit()
    usage = (await period(api_client, "/api/reports/usage", from_day=3, to_day=4)).json()
    row = next(r for r in usage["seats"] if r["seat_id"] == seats[2]["id"])
    assert row["occupied_hours"] == 1 and row["percent"] <= 100


def test_excel_ids_are_integers_and_text_is_not_formula():
    from openpyxl import Workbook, load_workbook

    from tg_studio.modules.coworking.report_excel import _finish

    wb = Workbook()
    ws = wb.active
    ws.append(["a", "b", "c", "d"])
    ws.append([1, 2, 12, "=1+1"])
    ws.append(["x", "y", 1.5, "@cmd"])
    loaded = load_workbook(BytesIO(_finish(wb)))
    cells = loaded.active
    assert cells["C2"].number_format == "General" and cells["C3"].number_format == "#,##0.00"
    assert cells["D2"].data_type == "s" and cells["D2"].value == "=1+1"


@pytest.mark.asyncio
async def test_dashboard_week_and_month_windows(api_client, reporting_facts, monkeypatch):
    from tg_studio.modules.coworking import reports as reports_module

    # 5 ноября 2026 — четверг: неделя 2–9 ноября, месяц весь ноябрь
    monkeypatch.setattr(reports_module, "studio_now", lambda: at(5, 12))
    week = (await api_client.get("/api/dashboard?period=week", headers=OWNER_HEADERS)).json()
    assert week["period"] == "week"
    assert week["from"].startswith("2026-11-02T00:00") and week["to"].startswith("2026-11-09T00:00")
    assert week["revenue"]["gross"] == 9000 and week["revenue"]["refunds"] == 1500
    month = (await api_client.get("/api/dashboard?period=month", headers=OWNER_HEADERS)).json()
    assert month["from"].startswith("2026-11-01T00:00") and month["to"].startswith("2026-12-01T00:00")
    assert month["revenue"]["gross"] == 9000
    day = (await api_client.get("/api/dashboard", headers=OWNER_HEADERS)).json()
    assert day["period"] == "day" and day["revenue"]["gross"] == 0
    assert (await api_client.get("/api/dashboard?period=year", headers=OWNER_HEADERS)).status_code == 422
