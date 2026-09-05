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


class UserRole(str, enum.Enum):
    owner = "owner"
    master = "master"
    client = "client"


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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    client: Mapped["Client | None"] = relationship(back_populates="user")
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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Google Calendar integration
    google_calendar_credentials_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_calendar_email: Mapped[str | None] = mapped_column(String(256), nullable=True)

    owner: Mapped["User"] = relationship()
    masters: Mapped[list["Master"]] = relationship(back_populates="business")
    services: Mapped[list["Service"]] = relationship(back_populates="business")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="client")


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
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)

    user: Mapped["User | None"] = relationship(back_populates="master")
    services: Mapped[list["MasterService"]] = relationship(back_populates="master")
    business: Mapped["Business"] = relationship(back_populates="masters")
    schedules: Mapped[list["WorkSchedule"]] = relationship(back_populates="master")
    projects: Mapped[list["TattooProject"]] = relationship(back_populates="master")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    business: Mapped["Business"] = relationship(back_populates="services")
    masters: Mapped[list["MasterService"]] = relationship(back_populates="service")


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


class AIConversation(Base):
    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    awaiting_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["AIMessage"]] = relationship(
        back_populates="conversation", order_by="AIMessage.created_at"
    )


class AIMessage(Base):
    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped["AIConversation"] = relationship(back_populates="messages")


class BookingStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False)
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("time_slots.id"), nullable=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), nullable=False)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus), nullable=False, default=BookingStatus.pending
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    cancel_deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    project_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Google Calendar event ID for sync
    google_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Relationships
    slot: Mapped["TimeSlot | None"] = relationship()


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TattooProjectStatus(str, enum.Enum):
    planned = "planned"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class TattooProject(Base):
    """
    Проект татуировки, создаваемый мастером через Mini App.

    Поля: размер, сложность, место нанесения, дата сеанса, стоимость.
    Привязывается к мастеру и синхронизируется с Google Calendar.
    """

    __tablename__ = "tattoo_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False, index=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)

    # Основные поля
    size: Mapped[str] = mapped_column(String(64), nullable=False)  # маленький, средний, большой
    complexity: Mapped[str] = mapped_column(String(64), nullable=False)  # низкая, средняя, высокая
    placement: Mapped[str] = mapped_column(String(256), nullable=False)  # место нанесения
    session_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Статус проекта
    status: Mapped[TattooProjectStatus] = mapped_column(
        Enum(TattooProjectStatus), nullable=False, default=TattooProjectStatus.planned
    )

    # Google Calendar event ID
    google_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Временные метки
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    master: Mapped["Master"] = relationship(back_populates="projects")
