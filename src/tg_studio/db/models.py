import enum
from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .types import UTCDateTime


class UserRole(str, enum.Enum):
    owner = "owner"
    master = "master"
    client = "client"
    # Роль администратора студии (коворкинг, docs/coworking/05) — добавляется
    # в PG enum отдельной нетранзакционной миграцией (ALTER TYPE ADD VALUE
    # нельзя в транзакции). До миграции в коде роль не используется.
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str | None] = mapped_column(String(256), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(256))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.client)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    master: Mapped["Master | None"] = relationship(back_populates="user")


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    owner_telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    # Google Calendar integration
    google_calendar_credentials_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_calendar_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Личный Google-адрес владельца, с которым расшарены календари (ACL-правило)
    google_share_email: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Настройки коворкинга (docs/coworking/02-03): max_seats, min_duration_minutes,
    # step_minutes, buffer_minutes, hold_ttl_hours. NULL = дефолты из
    # modules/coworking/service.py. Правится владельцем в «Настройке мест».
    coworking_settings: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON), nullable=True)

    owner: Mapped["User"] = relationship()
    masters: Mapped[list["Master"]] = relationship(back_populates="business")


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    registration_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Мягкое удаление: строка остаётся в БД (работы, платежи, портфолио —
    # для анализа), из списков мастеров пропадает. NULL = не удалён.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Google Calendar — secondary calendar within Business.google_calendar_credentials_json's
    # account. NULL means "not set up yet", callers fall back to "primary".
    google_calendar_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Фото профиля (аватар): относительный путь на диске upload_dir
    # (masters/avatar/<master_id>/…). NULL = не задан, показываем бейдж-инициалы.
    avatar_stored_path: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Дефолтная длительность записи (мин) — подставляется в форму записи,
    # реальную длительность задаёт конкретная запись. NULL = 60.
    default_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Резидент студии (коворкинг, docs/coworking/03): на него действуют
    # скидки типа resident_discount_percent. Правит владелец.
    is_resident: Mapped[bool] = mapped_column(Boolean, default=False)

    # Телефон мастера (коворкинг, 04): на него выставляются счета Kaspi
    # (apipay) за предоплату брони места. NULL — только ручные платежи.
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    user: Mapped["User | None"] = relationship(back_populates="master")
    business: Mapped["Business"] = relationship(back_populates="masters")
    portfolio_files: Mapped[list["MasterPortfolioFile"]] = relationship(
        back_populates="master"
    )


class MasterPortfolioFile(Base):
    """Фото портфолио мастера (загружает владелец из миниаппы).

    Файл лежит на диске в UPLOAD_DIR (masters/portfolio/<master_id>/),
    в БД — метаданные и относительный путь. Отдаётся публичным
    read-only эндпоинтом — маркетинговый материал для клиентов.
    """

    __tablename__ = "master_portfolio_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(
        ForeignKey("masters.id", ondelete="CASCADE"), nullable=False, index=True
    )

    stored_path: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    original_name: Mapped[str] = mapped_column(String(256), nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    master: Mapped["Master"] = relationship(back_populates="portfolio_files")


class PaymentKind(str, enum.Enum):
    """Тип платежа. Хранится строкой (.value) — как clients.source."""

    prepay = "prepay"    # предоплата брони
    partial = "partial"  # доплата в ходе работы (не закрывает баланс)
    final = "final"      # финальный расчёт


class PaymentStatus(str, enum.Enum):
    pending = "pending"      # счёт выставлен, ждёт оплаты
    paid = "paid"
    cancelled = "cancelled"  # счёт отменили (сами), не оплата
    expired = "expired"      # истёк по сроку (платёжная система)


class PaymentProvider(str, enum.Enum):
    manual = "manual"  # наличные / перевод — подтверждает мастер или владелец
    apipay = "apipay"  # счёт Kaspi по номеру — оплату подтверждает вебхук


class Payment(Base):
    """Платёж: счёт (pending) и его оплата (paid) — за бронь места (04) или продажу (06).

    Баланс мастера нигде не хранится — считается из ledger_entries.
    Бронь/продажа — ondelete=SET NULL: деньги уже прошли, теряется только ссылка.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Id счёта у платёжной системы (apipay); у manual — NULL
    external_invoice_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Кто выставил счёт (обе роли — Users) и чьё подтверждение оплаты
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id"), nullable=True)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    master: Mapped["Master | None"] = relationship()
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Коворкинг (06): счёт за продажу товаров
    sale_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales.id", ondelete="SET NULL"), nullable=True, index=True
    )


# ---------------------------------------------------------------------------
# Коворкинг: ledger — проводки счёта мастера (docs/coworking/04-money-prepay.md)
# ---------------------------------------------------------------------------


class LedgerEntryKind(str, enum.Enum):
    """Тип проводки. Хранится строкой (.value) — как clients.source."""

    topup = "topup"      # пополнение баланса (оплата счёта: карта/вебхук/manual)
    charge = "charge"    # списание с баланса (оплата брони места «с баланса»)
    refund = "refund"    # возврат (отмена брони по правилу / площадкой)
    adjust = "adjust"    # ручная корректировка владельцем/админом (основание обязательно)


class LedgerEntry(Base):
    """Append-only проводка счёта мастера (коворкинг, 04).

    Единственные хранимые факты о деньгах мастера. Остаток баланса НИГДЕ
    не хранится — вычисляется как Σ amount по проводкам мастера в бизнесе,
    поэтому журнал не может разойтись с балансом.

    amount со знаком: topup/refund > 0, charge < 0, adjust — любой знак.
    Ссылки: payment_id (topup по счёту), booking_id (charge/refund за бронь),
    sale_id — зарезервировано под продажи товаров (06). Автор — обязателен
    для adjust (и проставляется всегда, когда действие делает человек).
    """

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KZT")
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"), nullable=True
    )
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )
    # Зарезервировано под продажи товаров/услуг админом (06)
    sale_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_ledger_entries_master_created", "master_id", "created_at"),
        # одна оплата счёта = один topup (дедуп вебхука/sync/ручной отметки на уровне БД)
        Index(
            "uq_ledger_entries_topup_payment",
            "payment_id",
            unique=True,
            postgresql_where=text("kind = 'topup'"),
            sqlite_where=text("kind = 'topup'"),
        ),
    )

    master: Mapped["Master"] = relationship()


class SupplyMovementKind(str, enum.Enum):
    """Тип движения по складу. Хранится строкой (.value) — как clients.source."""

    init = "init"          # начальный остаток при создании позиции
    purchase = "purchase"  # закупка (пополнение)
    use = "use"            # списание (мастер или владелец)
    adjust = "adjust"      # ревизия: остаток выставлен вручную
    # Коворкинг (06): продажа и возврат товара
    sale = "sale"          # продажа мастеру / клиенту мастера
    consume = "consume"    # обеспечение площадки (расход студии, без оплаты)
    refund = "refund"      # возврат по продаже (сторнирующее движение)


class Supply(Base):
    """Позиция склада: справочник + денормализованный текущий остаток.

    История — в supply_movements; quantity обновляется транзакционно вместе
    со вставкой движения. Удаление — мягкое (is_active=False): журнал
    не должен терять позицию.
    """

    __tablename__ = "supplies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # Строки, не enum-типы в БД: справочник растим без миграций (как стили тату)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    # Порог «пора докупать»; NULL — за позицией не следим
    min_quantity: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Коворкинг (06): цены и себестоимость (NULL — позиция не продаётся)
    purchase_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    sale_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    movements: Mapped[list["SupplyMovement"]] = relationship(back_populates="supply")

    @property
    def low(self) -> bool:
        return self.min_quantity is not None and self.quantity <= self.min_quantity


class SupplyMovement(Base):
    """Движение по складу: кто, когда, сколько и зачем.

    delta со знаком: + закупка / начальный остаток, − списание. Для kind=use
    
    """

    __tablename__ = "supply_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    supply_id: Mapped[int] = mapped_column(ForeignKey("supplies.id"), nullable=False, index=True)
    delta: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text)

    # Коворкинг (06): автор движения и связь с продажей/бронью
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sale_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales.id", ondelete="SET NULL"), nullable=True
    )
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    supply: Mapped["Supply"] = relationship(back_populates="movements")
    master: Mapped["Master | None"] = relationship()


# ---------------------------------------------------------------------------
# Коворкинг: рабочие места и брони (docs/coworking/02-seats-bookings.md)
# ---------------------------------------------------------------------------


class BookingStatus(str, enum.Enum):
    """Статус брони места. Хранится строкой (.value) — как clients.source.

    Активные (занимают место, участвуют в проверке пересечений):
    ожидает_оплаты, подтверждена, начата. Терминальные — всё остальное.
    """

    pending_payment = "ожидает_оплаты"
    confirmed = "подтверждена"
    started = "начата"
    completed = "завершена"
    cancelled_by_master = "отменена_мастером"
    cancelled_by_studio = "отменена_площадкой"
    no_show = "неявка"
    payment_expired = "истекла_оплата"


ACTIVE_BOOKING_STATUSES = (
    BookingStatus.pending_payment.value,
    BookingStatus.confirmed.value,
    BookingStatus.started.value,
)

# Брони, освободившие место и не состоявшиеся (в календарь не попадают)
RELEASED_BOOKING_STATUSES = (
    BookingStatus.cancelled_by_master.value,
    BookingStatus.cancelled_by_studio.value,
    BookingStatus.payment_expired.value,
)


class BookingEventKind(str, enum.Enum):
    """Событие истории брони. Хранится строкой (.value)."""

    created = "создана"
    rescheduled = "перенесена"
    extended = "продлена"
    cancelled = "отменена"
    started = "начата"
    completed = "завершена"
    no_show = "неявка"
    payment_expired = "истекла_оплата"
    # Пересчёт цены при переносе/продлении (03): новый снимок в бронь
    priced = "пересчитана_цена"
    # Оплата/возврат по брони (04): детали — method, amount, payment_id, refund
    paid = "оплачена"
    refunded = "возвращена"
    # Посещение по брони (05): фактический приход/уход, автор — админ
    visit_started = "пришёл"
    visit_ended = "ушёл"
    # Подтверждение брони вне покрытия владельцем (05)
    confirmed = "подтверждена_владельцем"
    # Мастер подтвердил присутствие кнопкой в напоминании Telegram
    presence_confirmed = "присутствие_подтверждено"


class Seat(Base):
    """Рабочее место студии. В MVP мест ровно 4 (лимит в сервисе), сущность
    допускает N. Удаление — мягкое (is_active=False): история броней не теряется."""

    __tablename__ = "seats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Оснащение места (лампа, кресло, стол…) — свободный текст для UI
    equipment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )


class StudioHours(Base):
    """Часы работы студии: интервал для дня недели (0=пн … 6=вс).

    Интервал внутри одного дня (open < close, полночь не пересекаем).
    Если строк нет — дефолт 10:00–22:00 каждый день (см. сервис коворкинга).
    """

    __tablename__ = "studio_hours"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=пн … 6=вс
    open_time: Mapped[time] = mapped_column(Time, nullable=False)
    close_time: Mapped[time] = mapped_column(Time, nullable=False)


class SeatBlock(Base):
    """Блокировка места (уборка/ремонт) — занятый интервал в календаре.

    seat_id=NULL — блокировка всей студии (все места). Блокировки участвуют
    в проверке пересечений наравне с активными бронями.
    """

    __tablename__ = "seat_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    # NULL = вся студия
    seat_id: Mapped[int | None] = mapped_column(ForeignKey("seats.id"), nullable=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (Index("ix_seat_blocks_seat_start", "seat_id", "start"),)


class Booking(Base):
    """Бронь места мастером на интервал [start, end).

    Жизненный цикл: ожидает_оплаты → (оплата в 04) → подтверждена; ветки
    отменена_мастером / отменена_площадкой / истекла_оплата. Прошедшая
    подтверждённая бронь отдельным статусом не отмечается (время вышло —
    бронь состоялась). начата/завершена/неявка — устаревшие статусы ручных
    отметок админа, новыми бронями не используются. Активные статусы занимают
    место: пересекаться не могут.

    price_snapshot (JSON, заполняется в 03) фиксирует расчёт цены на момент
    брони — смена тарифов не трогает подтверждённые брони. paid_amount —
    сколько реально оплачено (04: 100% предоплата — подтверждённая бронь
    всегда оплачена полностью: paid_amount == price_snapshot.final).
    """

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id"), nullable=False)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BookingStatus.pending_payment.value
    )
    # Дедлайн оплаты: не оплатил к нему → истекла_оплата (beat-задача)
    hold_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Снимок расчёта цены (03/04): части + итог. NULL до подключения цен.
    price_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    paid_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    # Фактические отметки (05): NULL до начала/завершения
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Бронь вне покрытия смен (05): флаг «требует подтверждения владельцем»
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Факт посещения по брони (05): отметки прихода/ухода (автор — в booking_events)
    visit_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    visit_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Мастер подтвердил присутствие кнопкой в напоминании Telegram (аналитика владельца)
    presence_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Напоминания в Telegram отправлены (не слать повторно): «оплатите до …» и «скоро бронь»
    payment_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booking_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_bookings_seat_start", "seat_id", "start"),
        Index("ix_bookings_master_start", "master_id", "start"),
    )

    seat: Mapped["Seat"] = relationship()
    master: Mapped["Master"] = relationship()


class BookingEvent(Base):
    """Append-only история брони: кто, когда, что изменилось (детали — JSON)."""

    __tablename__ = "booking_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), index=True)

    booking: Mapped["Booking"] = relationship()


# ---------------------------------------------------------------------------
# Коворкинг: ценовые правила и скидки (docs/coworking/03-pricing-settings.md)
# ---------------------------------------------------------------------------


class PriceRuleDayKind(str, enum.Enum):
    """Область действия ценового правила по дням. Хранится строкой (.value)."""

    all = "все"  # любой день
    weekdays = "будни"  # пн–пт
    weekends = "выходные"  # сб–вс
    date = "дата"  # конкретная дата (праздник/особый день) — см. specific_date


class PriceRule(Base):
    """Ценовое правило: ставка за час на интервал времени суток.

    Область: seat_id=NULL — все места студии, иначе конкретное место.
    Дни: day_kind + specific_date (только для day_kind=date, локальная дата студии).
    Интервал времени суток [time_from, time_to) — локальное время студии;
    пересекающиеся правила разруливаются приоритетом (priority выше — раньше).
    Правило с is_active=False не применяется (но хранится — история).
    """

    __tablename__ = "price_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    # NULL = все места
    seat_id: Mapped[int | None] = mapped_column(ForeignKey("seats.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    day_kind: Mapped[str] = mapped_column(String(16), nullable=False, default=PriceRuleDayKind.all.value)
    # Только для day_kind="дата": локальная дата студии (Asia/Almaty)
    specific_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Интервал времени суток [time_from, time_to), локальное время студии
    time_from: Mapped[time] = mapped_column(Time, nullable=False)
    time_to: Mapped[time] = mapped_column(Time, nullable=False)
    # Ставка за час (в валюте студии, KZT); дробная часть — копейки
    hourly_rate: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_price_rules_seat", "seat_id"),)


class DiscountRule(Base):
    """Скидка на бронь (docs/coworking/03).

    kind: min_hours — при длительности брони от N часов (порог и размер задаёт
    владелец; скидки резидентам не считаются). percent — доля от base (0..1),
    fixed — фиксированная сумма в валюте студии. Ровно одно из двух задано.
    seat_id=NULL — все места.
    """

    __tablename__ = "discount_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    seat_id: Mapped[int | None] = mapped_column(ForeignKey("seats.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # "min_hours"
    # Для kind="min_hours": порог длительности в часах
    min_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    percent: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)  # 0..1
    fixed_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_discount_rules_seat", "seat_id"),)


# ---------------------------------------------------------------------------
# Коворкинг: администраторы, смены, апсейлы, выплаты (docs/coworking/05-admin-upsells.md)
# ---------------------------------------------------------------------------


class StudioAdmin(Base):
    """Администратор студии (роль UserRole.admin, назначается владельцем).

    Видит студию целиком (календарь, брони, продажи, склад, балансы — обзорно),
    отмечает визиты по броням, ведёт смены, принимает ручные оплаты и оформляет
    продажи (они становятся апсейлами — см. Upsell).
    """

    __tablename__ = "studio_admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    # Привязка к User — как у мастера: вход по initData находит эту строку
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    # Мягкое удаление: история (смен, апсейлы, выплаты) не теряется
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class Shift(Base):
    """Смена администратора: план (интервал), факт (начало/конец), утверждённые
    оплачиваемые часы (утверждает владелец).

    Часы покрытия студии считаются по сменам: пересекающиеся смены двух админов
    дают покрытие, а не сумму (см. сервис coverage_hours).
    """

    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    admin_id: Mapped[int] = mapped_column(ForeignKey("studio_admins.id"), nullable=False)
    # План
    planned_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Факт
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Утверждённые оплачиваемые часы (NULL — ещё не утверждали)
    approved_hours: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_shifts_business_admin", "business_id", "admin_id"),
        Index("ix_shifts_admin_start", "admin_id", "planned_start"),
    )

    admin: Mapped["StudioAdmin"] = relationship()


class Upsell(Base):
    """Апсейл: запись о продаже, оформленной администратором (05).

    При оформлении продажи админом (06) создаётся Upsell с admin_id — автором.
    По апсейлам владелец начисляет доначисления (Payout). До подключения 06
    sale_id=NULL, детали продажи — в note.
    """

    __tablename__ = "upsells"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    admin_id: Mapped[int] = mapped_column(ForeignKey("studio_admins.id"), nullable=False)
    # Продажа (06): ссылка на sale. NULL у апсейлов, зафиксированных вручную
    sale_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales.id", ondelete="SET NULL"), nullable=True
    )
    # Кому продано: мастер (продажа мастеру) или разовый контакт клиента мастера
    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id"), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    buyer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KZT")
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_upsells_business_admin", "business_id", "admin_id"),
        Index("ix_upsells_admin_created", "admin_id", "created_at"),
    )

    admin: Mapped["StudioAdmin"] = relationship()


class PayoutStatus(str, enum.Enum):
    """Статус доначисления администратору. Хранится строкой (.value)."""

    accrued = "accrued"  # начислено
    paid = "paid"        # выплачено


class Payout(Base):
    """Доначисление (payout) администратору за апсейлы (05).

    Владелец либо задаёт правило (% от суммы апсейлов за период — percent +
    period_from/period_to), либо фиксирует разовое доначисление с основанием
    (reason обязателен всегда). Статус: accrued → paid (выплата отмечается
    владельцем, paid_at + paid_by_user_id).
    """

    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    admin_id: Mapped[int] = mapped_column(ForeignKey("studio_admins.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KZT")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PayoutStatus.accrued.value
    )
    # Правило % от апсейлов за период (NULL у разового доначисления)
    percent: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    period_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    paid_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_payouts_business_admin", "business_id", "admin_id"),
        Index("ix_payouts_admin_status", "admin_id", "status"),
    )

    admin: Mapped["StudioAdmin"] = relationship()


# ---------------------------------------------------------------------------
# Коворкинг: продажи товаров со склада (docs/coworking/06-stock-sales.md)
# ---------------------------------------------------------------------------


class SaleStatus(str, enum.Enum):
    """Статус продажи. Хранится строкой (.value)."""

    paid = "оплачена"
    cancelled = "отменена"
    refunded = "возвращена"


class SalePaymentMethod(str, enum.Enum):
    """Способ оплаты продажи."""

    balance = "balance"  # с баланса мастера (ledger charge)
    cash = "cash"        # наличные (manual-платёж)
    card = "card"        # карта (manual-платёж; apipay-счёт — позже)


class Sale(Base):
    """Продажа товаров со склада (коворкинг, 06).

    Позиции фиксируют цену продажи и себестоимость на момент продажи —
    задним числом не меняются. Покупатель: мастер (master_id) ИЛИ разовый
    контакт клиента мастера (buyer_name/buyer_phone). Каждая продажа имеет
    автора; если автор — админ, создаётся Upsell с sale_id (05).
    """

    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    # Кому продано: мастер или разовый контакт (ровно один вариант)
    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id"), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    buyer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KZT")
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SaleStatus.paid.value
    )
    # Автор-админ (апсейл) и пользователь, оформивший продажу
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("studio_admins.id"), nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_sales_business_admin", "business_id", "admin_id"),
        Index("ix_sales_business_master", "business_id", "master_id"),
    )

    items: Mapped[list["SaleItem"]] = relationship(back_populates="sale", cascade="all, delete-orphan")
    master: Mapped["Master | None"] = relationship()
    admin: Mapped["StudioAdmin | None"] = relationship()


class SaleItem(Base):
    """Позиция продажи: товар, количество, зафиксированные цены."""

    __tablename__ = "sale_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    supply_id: Mapped[int] = mapped_column(ForeignKey("supplies.id"), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    sale: Mapped["Sale"] = relationship(back_populates="items")
    supply: Mapped["Supply"] = relationship()
