"""Публичные формы дашборда и отчётов владельца (07)."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class Period(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime
    model_config = {"populate_by_name": True}


class RevenueDay(BaseModel):
    date: date
    booking_revenue: float
    sales_revenue: float
    gross: float
    refunds: float
    net: float
    hours: float


class RevenueSeat(BaseModel):
    seat_id: int
    seat_name: str
    hours: float
    revenue: float


class Segment(BaseModel):
    segment: str
    hours: float
    percent: float | None = None


class RevenueReport(Period):
    currency: str
    booking_revenue: float
    sales_revenue: float
    gross: float
    booking_refunds: float
    sale_refunds: float
    refunds: float
    net: float
    by_day: list[RevenueDay]
    by_seat: list[RevenueSeat]
    segments: list[Segment]


class SeatUsage(BaseModel):
    seat_id: int
    seat_name: str
    is_active: bool
    available_hours: float
    occupied_hours: float
    blocked_hours: float
    free_hours: float
    percent: float
    segments: list[Segment]


class UsageReport(Period):
    seats: list[SeatUsage]
    available_hours: float
    occupied_hours: float
    blocked_hours: float
    percent: float
    segments: list[Segment]


class MoneyRow(BaseModel):
    date: datetime | None
    type: str
    booking_id: int | None = None
    sale_id: int | None = None
    master_name: str | None = None
    amount: float
    method: str | None = None
    external_id: str | None = None
    payment_id: int | None = None
    held: float | None = None
    note: str | None = None


class MasterBalance(BaseModel):
    master_id: int
    master_name: str
    balance: float
    is_active: bool


class MoneyReport(Period):
    currency: str
    receipts: float
    refunds: float
    net_cash: float
    booking_holds: float
    topups: float
    charges: float
    total_balance: float
    payments: list[MoneyRow]
    refund_rows: list[MoneyRow]
    balances: list[MasterBalance]


class MasterReportRow(BaseModel):
    master_id: int
    master_name: str
    is_active: bool
    is_new: bool
    bookings: int
    hours: float
    revenue: float
    booking_revenue: float
    sales_revenue: float
    refunds: float = 0
    balance: float
    # Присутствие (аналитика владельца): брони, уже начавшиеся в периоде, и из них подтверждённые мастером
    presence_expected: int = 0
    presence_confirmed: int = 0


class MastersReport(Period):
    currency: str
    new: int
    active: int
    masters: list[MasterReportRow]
    presence_expected: int = 0
    presence_confirmed: int = 0


class UpsellReportRow(BaseModel):
    admin_id: int
    admin_name: str
    count: int
    amount: float
    product_amount: float
    manual_amount: float
    master_amount: float
    client_amount: float
    payout_accrued: float
    payout_paid: float


class UpsellsReport(Period):
    currency: str
    amount: float
    master_amount: float
    client_amount: float
    admins: list[UpsellReportRow]


class DashboardBooking(BaseModel):
    booking_id: int
    master_name: str | None
    seat_name: str | None
    start: datetime
    end: datetime
    status: str | None = None
    # для «идут сейчас»: мастер подтвердил присутствие кнопкой в напоминании
    presence_confirmed: bool | None = None


class DashboardSeat(BaseModel):
    seat_id: int
    seat_name: str
    status: str
    booking_id: int | None
    master_name: str | None
    until: datetime | None
    upcoming: int


class LowStock(BaseModel):
    id: int
    name: str
    quantity: float
    unit: str
    min_quantity: float


class Dashboard(Period):
    generated_at: datetime
    period: str
    currency: str
    revenue: RevenueReport
    money: MoneyReport
    usage: UsageReport
    master_balances: float
    bookings_created: int
    cancellations: int
    onsite: list[DashboardBooking]
    upcoming: list[DashboardBooking]
    seats_now: list[DashboardSeat]
    low_stock: list[LowStock]
    exceptions: list[dict]
    upsells: UpsellsReport
