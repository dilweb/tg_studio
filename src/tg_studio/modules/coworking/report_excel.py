"""Excel для бухгалтера (07): выручка, возвраты/удержания, товары с себестоимостью."""

from datetime import datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from tg_studio.modules.coworking.reports import (
    CENT,
    PAID_BOOKING_STATUSES,
    ReportData,
    _booking_interval,
    _overlap_seconds,
    dec,
    money,
    utc,
)
from tg_studio.timeutil import to_studio_local

HEADER_FILL = PatternFill("solid", fgColor="DCE6F1")


def _local(value: datetime) -> datetime:
    """Excel не поддерживает aware datetime: локальное naive время студии."""
    return to_studio_local(value).replace(tzinfo=None)


def _sheet(workbook: Workbook, title: str, headers: list[str]):
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
    return sheet


def _finish(workbook: Workbook) -> bytes:
    for sheet in workbook:
        for column in range(1, sheet.max_column + 1):
            width = max(len(str(cell.value or "")) for cell in sheet[get_column_letter(column)][:1000])
            sheet.column_dimensions[get_column_letter(column)].width = min(max(width + 2, 10), 40)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, (int, float)) and cell.column > 2:
                    cell.number_format = "#,##0.00"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_revenue_xlsx(data: ReportData, revenue: dict, money_data: dict, from_: datetime, to: datetime) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.title = "Выручка коворкинга"
    masters = {master.id: master.full_name for master in data.masters}
    sales = {sale.id: sale for sale in data.sales}
    payments = {payment.id: payment for payment in data.payments}
    payments_by_booking = {}
    payments_by_sale = {}
    for payment in data.payments:
        if payment.booking_id:
            payments_by_booking.setdefault(payment.booking_id, []).append(payment)
        if payment.sale_id:
            payments_by_sale.setdefault(payment.sale_id, []).append(payment)

    sheet = _sheet(workbook, "Выручка", [
        "Дата", "Тип операции", "Мастер / покупатель", "Сумма, ₸", "Способ оплаты",
        "Поступление, ₸", "ID платежа", "Внешний ID", "Бронь", "Продажа", "Примечание",
    ])
    total = 0
    for booking in sorted(data.bookings, key=lambda b: utc(b.start)):
        if booking.status not in PAID_BOOKING_STATUSES:
            continue
        start, end = _booking_interval(booking)
        duration = (end - start).total_seconds()
        seconds = _overlap_seconds(start, end, from_, to)
        if not duration or not seconds:
            continue
        amount = dec(booking.paid_amount) * dec(seconds) / dec(duration)
        linked = payments_by_booking.get(booking.id, [])
        external = next((p for p in linked if p.external_invoice_id), None)
        sheet.append([
            _local(start), f"Бронь: место #{booking.seat_id}", masters.get(booking.master_id),
            money(amount), "баланс" if not linked else " / ".join(p.provider for p in linked),
            money(sum((dec(p.amount) for p in linked), dec(0)) * dec(seconds) / dec(duration)),
            next((p.id for p in linked if p.id is not None), None),
            external.external_invoice_id if external else None,
            booking.id, None, "Выручка распределена по часам брони" if seconds != dec(duration) else None,
        ])
        total += amount
    for sale in data.sales:
        if not (from_ <= utc(sale.created_at) < to):
            continue
        linked = payments_by_sale.get(sale.id, [])
        sheet.append([
            _local(sale.created_at), "Продажа", masters.get(sale.master_id) or sale.buyer_name,
            money(dec(sale.total_amount)), sale.payment_method,
            money(sum((dec(p.amount) for p in linked), dec(0))),
            linked[0].id if linked else None,
            next((p.external_invoice_id for p in linked if p.external_invoice_id), None),
            sale.booking_id, sale.id, sale.note,
        ])
        total += dec(sale.total_amount)
    sheet.append(["ИТОГО", None, None, money(total), None, money(dec(money_data["receipts"]))])
    sheet.cell(sheet.max_row, 1).font = sheet.cell(sheet.max_row, 4).font = Font(bold=True)

    refunds_sheet = _sheet(workbook, "Возвраты и удержания", [
        "Дата", "Тип", "Мастер / покупатель", "Возврат, ₸", "Удержание, ₸", "Способ",
        "ID платежа", "Внешний ID", "Бронь", "Продажа", "Примечание",
    ])
    for row in sorted(money_data["refund_rows"], key=lambda r: utc(r["date"])):
        sale = sales.get(row["sale_id"])
        payment = payments.get(row["payment_id"])
        refunds_sheet.append([
            _local(row["date"]), row["type"], row["master_name"] or (sale.buyer_name if sale else None),
            row["amount"], row["held"], "баланс" if row["master_name"] else "наличные / карта",
            row["payment_id"], payment.external_invoice_id if payment else None,
            row["booking_id"], row["sale_id"], row["note"],
        ])
    refunds_sheet.append([
        "ИТОГО", None, None, money_data["refunds"], money_data["booking_holds"],
    ])

    products_sheet = _sheet(workbook, "Продажи товаров", [
        "Дата", "Продажа", "Покупатель", "Товар", "Количество", "Цена", "Сумма, ₸",
        "Себестоимость ед.", "Себестоимость, ₸", "Маржа, ₸", "Способ оплаты", "ID платежа",
    ])
    cost_total = Decimal("0")
    amount_total = Decimal("0")
    for sale in data.sales:
        if not (from_ <= utc(sale.created_at) < to):
            continue
        linked = payments_by_sale.get(sale.id, [])
        for item in sale.items:
            cost = (dec(item.unit_cost) * dec(item.qty)).quantize(CENT)
            products_sheet.append([
                _local(sale.created_at), sale.id, masters.get(sale.master_id) or sale.buyer_name,
                item.supply.name if item.supply else f"Товар #{item.supply_id}", float(item.qty),
                float(item.unit_price), float(item.amount), float(item.unit_cost), float(cost),
                money(dec(item.amount) - cost), sale.payment_method, linked[0].id if linked else None,
            ])
            amount_total += dec(item.amount)
            cost_total += cost
    products_sheet.append([
        "ИТОГО", None, None, None, None, None, money(amount_total), None,
        money(cost_total), money(amount_total - cost_total),
    ])
    return _finish(workbook)
