"""Pydantic-схемы коворкинга: места, часы, блокировки, брони, календари, цены."""

from datetime import date, datetime, time

from pydantic import BaseModel, Field, field_validator, model_validator

from tg_studio.timeutil import is_valid_window

# ---------------------------------------------------------------------------
# Места
# ---------------------------------------------------------------------------


class SeatCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    equipment: str | None = Field(None, max_length=2000)


class SeatUpdate(BaseModel):
    """PATCH-семантика: переданное меняем; None в equipment — очистить."""

    name: str | None = Field(None, min_length=1, max_length=64)
    equipment: str | None = Field(None, max_length=2000)
    is_active: bool | None = None
    sort_order: int | None = Field(None, ge=0)


class SeatOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    equipment: str | None
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SeatListResponse(BaseModel):
    seats: list[SeatOut]


# ---------------------------------------------------------------------------
# Часы работы студии
# ---------------------------------------------------------------------------


class StudioHoursEntry(BaseModel):
    weekday: int = Field(..., ge=0, le=6)  # 0=пн … 6=вс
    open_time: time
    close_time: time


class StudioHoursUpdate(BaseModel):
    """Полная замена расписания студии (список интервалов по дням)."""

    entries: list[StudioHoursEntry] = Field(..., max_length=14)


class StudioHoursEntryOut(BaseModel):
    model_config = {"from_attributes": True}

    weekday: int
    open_time: time
    close_time: time


class StudioHoursResponse(BaseModel):
    entries: list[StudioHoursEntryOut]


# ---------------------------------------------------------------------------
# Настройки коворкинга (настройки мест: лимиты, шаг, буфер, TTL hold)
# ---------------------------------------------------------------------------


class RefundTier(BaseModel):
    """Ступень возврата при отмене мастером: отмена не позже чем за hours_before
    часов до начала → percent % возврата."""

    hours_before: int = Field(..., ge=0, le=720)
    percent: int = Field(..., ge=0, le=100)


class CoworkingSettingsOut(BaseModel):
    """Эффективные настройки (дефолты смешаны с переопределениями из БД)."""

    max_seats: int
    min_duration_minutes: int
    step_minutes: int
    buffer_minutes: int
    hold_ttl_hours: int
    # Ступени возврата при отмене мастером (от большей к меньшей); ни одна не
    # подошла → 0%. Отмена площадкой — всегда 100%.
    refund_tiers: list[RefundTier]
    # Напоминания в Telegram: «скоро бронь» и «оплатите до …» — за N минут
    booking_reminder_minutes: int
    payment_reminder_minutes: int


class CoworkingSettingsUpdate(BaseModel):
    max_seats: int | None = Field(None, ge=1, le=100)
    min_duration_minutes: int | None = Field(None, ge=15, le=720)
    step_minutes: int | None = Field(None, ge=5, le=120)
    buffer_minutes: int | None = Field(None, ge=0, le=240)
    hold_ttl_hours: int | None = Field(None, ge=1, le=168)
    refund_tiers: list[RefundTier] | None = Field(None, max_length=10)
    booking_reminder_minutes: int | None = Field(None, ge=5, le=1440)
    payment_reminder_minutes: int | None = Field(None, ge=5, le=1440)

    @field_validator("refund_tiers")
    @classmethod
    def _tiers_consistent(cls, tiers: list[RefundTier] | None) -> list[RefundTier] | None:
        """Часы ступеней разные; чем раньше отмена, тем возврат не меньше."""
        if tiers is None:
            return None
        ordered = sorted(tiers, key=lambda t: t.hours_before, reverse=True)
        if len({t.hours_before for t in ordered}) != len(ordered):
            raise ValueError("У ступеней возврата часы не должны повторяться")
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.percent < later.percent:
                raise ValueError("Чем раньше отмена, тем возврат должен быть не меньше")
        return ordered


# ---------------------------------------------------------------------------
# Ценовые правила и скидки (03)
# ---------------------------------------------------------------------------


PRICE_DAY_KINDS = ("все", "будни", "выходные", "дата")
DISCOUNT_KINDS = ("min_hours",)  # скидки резидентам не считаем — тариф резидента владелец задаёт сам


class PriceRuleCreate(BaseModel):
    """seat_id=None — правило на все места. day_kind="дата" требует specific_date.
    time_to=00:00 — до полуночи."""

    seat_id: int | None = None
    name: str = Field(..., min_length=1, max_length=128)
    day_kind: str = Field("все")  # все | будни | выходные | дата
    specific_date: date | None = None
    time_from: time
    time_to: time
    hourly_rate: float = Field(..., gt=0)
    priority: int = 0
    is_active: bool = True

    @model_validator(mode="after")
    def _check_window(self):
        if not is_valid_window(self.time_from, self.time_to):
            raise ValueError("time_from должен быть раньше time_to (или time_to=00:00 — до полуночи)")
        if self.day_kind == "дата" and self.specific_date is None:
            raise ValueError("Для day_kind='дата' укажите specific_date")
        if self.day_kind not in PRICE_DAY_KINDS:
            raise ValueError("day_kind: все | будни | выходные | дата")
        return self


class PriceRuleUpdate(BaseModel):
    seat_id: int | None = None
    name: str | None = Field(None, min_length=1, max_length=128)
    day_kind: str | None = None
    specific_date: date | None = None
    time_from: time | None = None
    time_to: time | None = None
    hourly_rate: float | None = Field(None, gt=0)
    priority: int | None = None
    is_active: bool | None = None

    @field_validator("day_kind")
    @classmethod
    def _day_kind(cls, v):
        if v is not None and v not in PRICE_DAY_KINDS:
            raise ValueError("day_kind: все | будни | выходные | дата")
        return v


class PriceRuleOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    business_id: int
    seat_id: int | None
    name: str
    day_kind: str
    specific_date: date | None
    time_from: time
    time_to: time
    hourly_rate: float
    priority: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PriceRuleListResponse(BaseModel):
    rules: list[PriceRuleOut]


class DiscountRuleCreate(BaseModel):
    """kind: min_hours (при длительности от N часов; порог и размер задаёт владелец).
    Ровно одно из percent (0..1) / fixed_amount должно быть задано."""

    seat_id: int | None = None
    name: str = Field(..., min_length=1, max_length=128)
    kind: str = "min_hours"
    min_hours: int | None = Field(None, ge=1, le=720)
    percent: float | None = Field(None, gt=0, le=1)
    fixed_amount: float | None = Field(None, gt=0)
    priority: int = 0
    is_active: bool = True

    @model_validator(mode="after")
    def _check(self):
        if self.kind not in DISCOUNT_KINDS:
            raise ValueError("kind: min_hours")
        if self.min_hours is None:
            raise ValueError("Для kind='min_hours' укажите min_hours")
        if (self.percent is None) == (self.fixed_amount is None):
            raise ValueError("Задайте ровно одно: percent или fixed_amount")
        return self


class DiscountRuleUpdate(BaseModel):
    seat_id: int | None = None
    name: str | None = Field(None, min_length=1, max_length=128)
    kind: str | None = None
    min_hours: int | None = Field(None, ge=1, le=720)
    percent: float | None = Field(None, gt=0, le=1)
    fixed_amount: float | None = Field(None, gt=0)
    priority: int | None = None
    is_active: bool | None = None

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v is not None and v not in DISCOUNT_KINDS:
            raise ValueError("kind: min_hours")
        return v


class DiscountRuleOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    business_id: int
    seat_id: int | None
    name: str
    kind: str
    min_hours: int | None
    percent: float | None
    fixed_amount: float | None
    priority: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DiscountRuleListResponse(BaseModel):
    rules: list[DiscountRuleOut]


# ---------------------------------------------------------------------------
# Калькулятор цены (quote) — не создаёт бронь
# ---------------------------------------------------------------------------


class PricingQuoteRequest(BaseModel):
    seat_id: int
    start: datetime
    end: datetime
    # Для(owner) можно указать мастера — чтобы применились резидентские скидки
    master_id: int | None = None


class PricingQuotePart(BaseModel):
    start: str
    end: str
    rule_id: int
    rule_name: str
    hourly_rate: float
    minutes: int
    amount: float


class PricingQuoteDiscount(BaseModel):
    rule_id: int
    name: str
    kind: str
    amount: float


class PricingQuoteResponse(BaseModel):
    """Снимок расчёта — тот же словарь, что уйдёт в bookings.price_snapshot."""

    currency: str
    calculated_at: str
    seat_id: int
    master_id: int | None
    start: str
    end: str
    parts: list[PricingQuotePart]
    base: float
    discounts: list[PricingQuoteDiscount]
    discount: float
    final: float


# ---------------------------------------------------------------------------
# Блокировки
# ---------------------------------------------------------------------------


class BlockCreate(BaseModel):
    """seat_id=None — блокировка всей студии."""

    seat_id: int | None = None
    start: datetime
    end: datetime
    reason: str = Field(..., min_length=1, max_length=256)


class BlockOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    seat_id: int | None
    start: datetime
    end: datetime
    reason: str
    created_by_user_id: int
    created_at: datetime


class BlockListResponse(BaseModel):
    blocks: list[BlockOut]


# ---------------------------------------------------------------------------
# Брони
# ---------------------------------------------------------------------------


class BookingCreate(BaseModel):
    seat_id: int
    # Наивное время — время студии (Asia/Almaty)
    start: datetime
    end: datetime
    note: str | None = Field(None, max_length=2000)
    # Только владелец: за какого мастера бронь (мастер всегда бронирует на себя)
    master_id: int | None = None


class BookingReschedule(BaseModel):
    """Перенос/продление: новый интервал (может меняться и start, и end)."""

    start: datetime
    end: datetime
    note: str | None = Field(None, max_length=2000)


class BookingExtend(BaseModel):
    """Продление: на сколько целых часов сдвинуть конец брони."""

    hours: int = Field(..., ge=1, le=12)
    note: str | None = Field(None, max_length=2000)


class BookingCancel(BaseModel):
    reason: str | None = Field(None, max_length=500)


class BookingPaymentOut(BaseModel):
    """Счёт за бронь (04): id, статус, url оплаты, дедлайн."""

    id: int
    amount: float
    status: str
    provider: str
    payment_url: str | None
    expires_at: datetime | None


class BookingOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    seat_id: int
    seat_name: str | None = None
    master_id: int
    master_name: str | None = None
    start: datetime
    end: datetime
    status: str
    hold_deadline: datetime | None
    price_snapshot: dict | None
    paid_amount: float
    # Счёт на предоплату (04) — для экрана оплаты брони
    payment: BookingPaymentOut | None = None
    # Бронь вне покрытия (05)
    requires_confirmation: bool = False
    confirmed_by_user_id: int | None = None
    # Мастер подтвердил присутствие кнопкой в напоминании Telegram
    presence_confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BookingListResponse(BaseModel):
    bookings: list[BookingOut]
    total: int  # всего броней под фильтр (страница — limit/offset)
    limit: int
    offset: int


class BookingEventOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    kind: str
    actor_user_id: int | None
    details: dict | None
    created_at: datetime


class BookingHistoryResponse(BaseModel):
    events: list[BookingEventOut]


class AvailabilitySlot(BaseModel):
    seat_id: int
    seat_name: str


class AvailabilityResponse(BaseModel):
    """Свободные на интервал [start, end) места (активные + неактивные — все)."""

    start: datetime
    end: datetime
    free_seats: list[AvailabilitySlot]


# ---------------------------------------------------------------------------
# Календари
# ---------------------------------------------------------------------------


class CalendarEntry(BaseModel):
    """Одна запись календаря: бронь или блокировка (поле kind)."""

    kind: str  # "booking" | "block" | "busy" (мастеру — чужое занятое время)
    id: int
    seat_id: int | None
    start: datetime
    end: datetime
    status: str | None = None  # у блокировок None
    reason: str | None = None  # у блокировок — причина
    master_id: int | None = None
    master_name: str | None = None
    seat_name: str | None = None


class CalendarResponse(BaseModel):
    model_config = {"populate_by_name": True}

    from_: datetime = Field(alias="from")
    to: datetime
    entries: list[CalendarEntry]


# ---------------------------------------------------------------------------
# Деньги коворкинга (04): баланс мастера, ledger, оплата брони
# ---------------------------------------------------------------------------


class BalanceOut(BaseModel):
    """Остаток счёта мастера — вычислен как Σ проводок (не хранится)."""

    master_id: int
    balance: float
    currency: str


class LedgerEntryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    master_id: int
    master_name: str | None = None
    kind: str  # topup | charge | refund | adjust
    amount: float  # со знаком
    currency: str
    payment_id: int | None
    booking_id: int | None
    actor_user_id: int | None
    note: str | None
    created_at: datetime


class LedgerListResponse(BaseModel):
    entries: list[LedgerEntryOut]
    total: int


class LedgerAdjustRequest(BaseModel):
    """Ручная корректировка баланса — основание обязательно."""

    amount: float  # со знаком: + пополнение, − списание
    reason: str = Field(..., min_length=1, max_length=2000)


class TopupRequest(BaseModel):
    """Ручное пополнение баланса мастера (наличные/перевод, принял владелец/админ)."""

    amount: float = Field(..., gt=0)
    note: str | None = Field(None, max_length=2000)


class MyTopupRequest(BaseModel):
    """Пополнение своего баланса картой (счёт Kaspi)."""

    amount: float = Field(..., gt=0)


class MyTopupResponse(BaseModel):
    payment_id: int
    amount: float
    payment_url: str | None
    expires_at: datetime | None
    currency: str


class TopupResponse(BaseModel):
    payment_id: int
    balance: float
    currency: str


class BookingPayRequest(BaseModel):
    """Оплата брони: картой (apipay-счёт уже создан) или с баланса мастера."""

    method: str  # "card" | "balance"


class BookingPayResponse(BaseModel):
    booking_id: int
    status: str
    paid_amount: float
    price: float
    # При method=card — куда платить (если счёт apipay); при balance — остаток после списания
    payment_url: str | None = None
    balance: float | None = None
    currency: str


# ---------------------------------------------------------------------------
# Администраторы, смены, апсейлы, выплаты, исключения (05)
# ---------------------------------------------------------------------------


class AdminCreate(BaseModel):
    """Назначить администратора по telegram_id (владелец)."""

    telegram_id: int
    full_name: str = Field(..., min_length=1, max_length=256)


class AdminOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    business_id: int
    user_id: int
    telegram_id: int
    full_name: str
    is_active: bool
    created_at: datetime


class AdminListResponse(BaseModel):
    admins: list[AdminOut]


class ShiftCreate(BaseModel):
    admin_id: int
    planned_start: datetime
    planned_end: datetime
    note: str | None = Field(None, max_length=2000)


class ShiftUpdate(BaseModel):
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    note: str | None = None


class ShiftStartEnd(BaseModel):
    """Факт начала/конца смены (время — фактическое, подставляется сервером)."""

    note: str | None = Field(None, max_length=2000)


class ShiftApprove(BaseModel):
    """Утверждение оплачиваемых часов смены владельцем."""

    approved_hours: float = Field(..., ge=0, le=24 * 31)


class ShiftOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    business_id: int
    admin_id: int
    admin_name: str | None = None
    planned_start: datetime
    planned_end: datetime
    started_at: datetime | None
    ended_at: datetime | None
    approved_hours: float | None
    approved_by_user_id: int | None
    approved_at: datetime | None
    note: str | None
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime


class ShiftListResponse(BaseModel):
    shifts: list[ShiftOut]


class CoverageOut(BaseModel):
    """Покрытие студии сменами на интервале: слитые интервалы + часы."""

    from_: datetime = Field(alias="from")
    to: datetime
    intervals: list[dict]  # [{start, end}]
    hours: float

    model_config = {"populate_by_name": True}


class UpsellCreate(BaseModel):
    """Зафиксировать апсейл (продажу, оформленную админом).

    Админ не передаёт admin_id — автором становится он сам; владелец обязан
    указать admin_id (на кого фиксируется апсейл).
    """

    admin_id: int | None = None  # только для владельца
    amount: float = Field(..., gt=0)
    master_id: int | None = None  # кому продано — мастеру
    buyer_name: str | None = Field(None, max_length=256)  # разовый контакт клиента мастера
    buyer_phone: str | None = Field(None, max_length=32)
    booking_id: int | None = None
    note: str | None = Field(None, max_length=2000)


class UpsellOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    business_id: int
    admin_id: int
    admin_name: str | None = None
    sale_id: int | None
    master_id: int | None
    buyer_type: str  # master — мастеру (расходники для работы) | client — клиенту мастера
    buyer_name: str | None
    buyer_phone: str | None
    amount: float
    currency: str
    booking_id: int | None
    note: str | None
    created_by_user_id: int
    created_at: datetime


class UpsellListResponse(BaseModel):
    upsells: list[UpsellOut]
    total: int


class UpsellSummary(BaseModel):
    """Сводка по апсейлам админа за период (для доначисления по правилу %)."""

    admin_id: int
    admin_name: str | None
    from_: datetime = Field(alias="from")
    to: datetime
    count: int
    total_amount: float
    currency: str

    model_config = {"populate_by_name": True}


class PayoutCreate(BaseModel):
    """Доначисление админу: либо фиксированная сумма с основанием,
    либо % от апсейлов за период (percent + from + to)."""

    admin_id: int
    amount: float | None = Field(None, gt=0)
    percent: float | None = Field(None, gt=0, le=1)
    from_: datetime | None = Field(None, alias="from")
    to: datetime | None = None
    reason: str = Field(..., min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Основание не должно быть пустым")
        return v

    @model_validator(mode="after")
    def _check(self):
        if (self.amount is None) == (self.percent is None):
            raise ValueError("Задайте ровно одно: amount (разовое) или percent (от апсейлов за период)")
        if self.percent is not None and (self.from_ is None or self.to is None):
            raise ValueError("Для percent укажите период: from и to")
        if self.from_ is not None and self.to is not None and self.to <= self.from_:
            raise ValueError("to должен быть позже from")
        return self


class PayoutOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    business_id: int
    admin_id: int
    admin_name: str | None = None
    amount: float
    currency: str
    status: str
    percent: float | None
    period_from: datetime | None
    period_to: datetime | None
    reason: str
    created_by_user_id: int
    paid_by_user_id: int | None
    paid_at: datetime | None
    created_at: datetime


class PayoutListResponse(BaseModel):
    payouts: list[PayoutOut]
    total: int


class OpsExceptionOut(BaseModel):
    kind: str  # payment_expired | out_of_coverage_unconfirmed
    booking_id: int
    seat_name: str | None
    master_name: str | None
    start: datetime
    end: datetime
    status: str


class OpsExceptionsResponse(BaseModel):
    exceptions: list[OpsExceptionOut]


class BookingConfirmOut(BaseModel):
    """Подтверждение брони вне покрытия владельцем (05)."""

    booking_id: int
    requires_confirmation: bool
    confirmed_by_user_id: int | None
