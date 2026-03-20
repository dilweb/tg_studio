import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ServiceType(str, enum.Enum):
    appointment = "appointment"  # почасовая запись на слот
    project = "project"          # проектная работа с фиксированной ценой


class BookingStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    in_progress = "in_progress"  # проект взят в работу
    cancelled = "cancelled"
    completed = "completed"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    refunded = "refunded"
    failed = "failed"
    forfeited = "forfeited"  # предоплата сгорела при отмене после дедлайна


class Business(Base):
    """Бизнес — владелец аккаунта в системе (студия, салон, клиника и т.д.)."""

    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Freedom Pay (multi-merchant)
    freedom_pay_merchant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    freedom_pay_secret_key: Mapped[str | None] = mapped_column(String(256), nullable=True)

    masters: Mapped[list["Master"]] = relationship(back_populates="business")
    services: Mapped[list["Service"]] = relationship(back_populates="business")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    bookings: Mapped[list["Booking"]] = relationship(back_populates="client")


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    # Одноразовая ссылка для привязки Telegram: t.me/bot?start=master_<token>
    registration_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    services: Mapped[list["MasterService"]] = relationship(back_populates="master")
    business: Mapped["Business"] = relationship(back_populates="masters")
    schedules: Mapped[list["WorkSchedule"]] = relationship(back_populates="master")
    slots: Mapped[list["TimeSlot"]] = relationship(back_populates="master")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="master")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    service_type: Mapped[ServiceType] = mapped_column(
        Enum(ServiceType), nullable=False, default=ServiceType.appointment
    )
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    prepayment_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    cancel_deadline_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    business: Mapped["Business"] = relationship(back_populates="services")
    masters: Mapped[list["MasterService"]] = relationship(back_populates="service")
    bookings: Mapped[list["Booking"]] = relationship(back_populates="service")


class WorkSchedule(Base):
    """
    Рабочее расписание мастера по дням недели.
    Одна запись = один рабочий день недели.
    Пример: мастер работает пн/ср/пт с 10:00 до 18:00.
    """

    __tablename__ = "work_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=пн, 6=вс
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "10:00"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)    # "18:00"
    slot_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    master: Mapped["Master"] = relationship(back_populates="schedules")


class MasterService(Base):
    """Связь мастер ↔ услуга (many-to-many)."""

    __tablename__ = "master_services"

    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), primary_key=True)

    master: Mapped["Master"] = relationship(back_populates="services")
    service: Mapped["Service"] = relationship(back_populates="masters")


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    master: Mapped["Master"] = relationship(back_populates="slots")
    booking: Mapped["Booking | None"] = relationship(back_populates="slot")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False)
    # NULL для project-типа — слот не нужен
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("time_slots.id"), nullable=True, unique=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus), default=BookingStatus.pending
    )
    # NULL для project-типа
    duration_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    cancel_deadline_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Срок сдачи проекта — только для project-типа
    project_deadline: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    client: Mapped["Client"] = relationship(back_populates="bookings")
    master: Mapped["Master"] = relationship(back_populates="bookings")
    slot: Mapped["TimeSlot | None"] = relationship(back_populates="booking")
    service: Mapped["Service"] = relationship(back_populates="bookings")
    payment: Mapped["Payment | None"] = relationship(back_populates="booking")
    chat_messages: Mapped[list["BookingChatMessage"]] = relationship(back_populates="booking")


class BookingChatMessage(Base):
    """Сообщение в чате клиент–мастер по брони (после оплаты)."""
    __tablename__ = "booking_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "client" | "master"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    booking: Mapped["Booking"] = relationship(back_populates="chat_messages")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False, unique=True)
    gateway_order_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    gateway: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "freedompay" | "kaspi"
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.pending
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)

    booking: Mapped["Booking"] = relationship(back_populates="payment")
