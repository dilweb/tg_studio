import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
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
    # Личный Google-адрес владельца, с которым расшарены календари (ACL-правило)
    google_share_email: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Прайс-конфиг (глобальный %, ставки размера, коэффициенты стилей/зон,
    # cover-up) — правится владельцем в разделе «Бизнес». NULL = прайс по
    # умолчанию из tattoo/pricing.py. См. effective_pricing().
    pricing_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    owner: Mapped["User"] = relationship()
    masters: Mapped[list["Master"]] = relationship(back_populates="business")


class ClientSource(str, enum.Enum):
    """Откуда пришёл клиент."""

    telegram = "tg"
    manual = "manual"
    instagram = "instagram"


class Client(Base):
    """Клиент. PK — суррогатный id; telegram/телефон/инста — идентификаторы
    каналов (nullable, уникальные). Ручной клиент может пока не иметь ни
    одного канала — бот позже допишет telegram_id в эту же строку."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    instagram_username: Mapped[str | None] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    # Метка канала (tg/manual/instagram) — строка, как и прочие справочники;
    # ClientSource — константы. Значения в БД — .value.
    source: Mapped[str] = mapped_column(
        String(16), default=ClientSource.telegram.value
    )
    note: Mapped[str | None] = mapped_column(Text)
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

    # Google Calendar — secondary calendar within Business.google_calendar_credentials_json's
    # account. NULL means "not set up yet", callers fall back to "primary".
    google_calendar_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Дефолтная длительность записи (мин) — подставляется в форму записи,
    # реальную длительность задаёт конкретная запись. NULL = 60.
    default_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped["User | None"] = relationship(back_populates="master")
    business: Mapped["Business"] = relationship(back_populates="masters")
    works: Mapped[list["TattooWork"]] = relationship(back_populates="master")


class AIConversation(Base):
    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    # Ровно одно из двух: user_id — владелец в аналитическом чате, client_id — клиент в чате записи
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
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


class ChatDirection(str, enum.Enum):
    from_client = "from_client"
    from_master = "from_master"


class ChatMessage(Base):
    """Сообщение в чате клиента с мастерской (через Telegram-бота)."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    direction: Mapped[ChatDirection] = mapped_column(
        Enum(ChatDirection, length=16), nullable=False
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Telegram file_id: у входящих — фото клиента, у исходящих — фото, которое мы отправили
    telegram_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    file_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "photo"
    # id сообщения в Telegram (только для исходящих — для отладки/дедупликации)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Непрочитанное входящее: read_at IS NULL. Общее для owner/master (один бизнес).
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TattooWorkStatus(str, enum.Enum):
    in_progress = "in_progress"  # ещё будут сеансы
    completed = "completed"
    cancelled = "cancelled"


class TattooProjectStatus(str, enum.Enum):
    planned = "planned"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class TattooWork(Base):
    """
    Тату целиком — то, что видит клиент как один заказ.

    Может состоять из нескольких сеансов (TattooSession).
    """

    __tablename__ = "tattoo_works"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"), nullable=False, index=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)

    # Размер тату в сантиметрах: длина × высота (выбирается числами, не enum)
    size_length_cm: Mapped[float] = mapped_column(Numeric(6, 1), nullable=False)
    size_height_cm: Mapped[float] = mapped_column(Numeric(6, 1), nullable=False)
    complexity: Mapped[str] = mapped_column(String(64), nullable=False)  # низкая, средняя, высокая
    # Стиль и зона — строки из справочника TattooStyle/TattooPlacement
    # (tattoo/schemas.py): валидация на входе, справочник растит без миграций
    style: Mapped[str] = mapped_column(String(64), nullable=False)
    placement: Mapped[str] = mapped_column(String(256), nullable=False)

    status: Mapped[TattooWorkStatus] = mapped_column(
        Enum(TattooWorkStatus), nullable=False, default=TattooWorkStatus.in_progress
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    master: Mapped["Master"] = relationship(back_populates="works")
    # Удаление работы уносит сеансы, сеансы — свои фото (файлы стирает API)
    sessions: Mapped[list["TattooSession"]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )


class TattooSession(Base):
    """
    Один сеанс в рамках TattooWork.

    Эскиз хранится здесь, а не на TattooWork — между сеансами он может меняться.
    Синхронизируется с Google Calendar.
    """

    __tablename__ = "tattoo_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("tattoo_works.id"), nullable=False, index=True)

    session_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Рекомендуемая цена по прайсу (tattoo/pricing.py) — для счёта на предоплату;
    # для дополнительного сеанса может быть задана вручную.
    recommended_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    # Фактически взято — за сколько договорились с клиентом; известно после
    # сеанса, поэтому nullable
    cost: Mapped[float | None] = mapped_column(Numeric(10, 2))
    status: Mapped[TattooProjectStatus] = mapped_column(
        Enum(TattooProjectStatus), nullable=False, default=TattooProjectStatus.planned
    )
    google_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    sketch_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    result_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_final_session: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # LLM-валидация результата сеанса
    llm_verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_observed_size: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_observed_color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_observed_style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    work: Mapped["TattooWork"] = relationship(back_populates="sessions")
    files: Mapped[list["TattooFile"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class SupplyMovementKind(str, enum.Enum):
    """Тип движения по складу. Хранится строкой (.value) — как clients.source."""

    init = "init"          # начальный остаток при создании позиции
    purchase = "purchase"  # закупка (пополнение)
    use = "use"            # списание (мастер или владелец)
    adjust = "adjust"      # ревизия: остаток выставлен вручную


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
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    movements: Mapped[list["SupplyMovement"]] = relationship(back_populates="supply")

    @property
    def low(self) -> bool:
        return self.min_quantity is not None and self.quantity <= self.min_quantity


class SupplyMovement(Base):
    """Движение по складу: кто, когда, сколько и зачем.

    delta со знаком: + закупка / начальный остаток, − списание. Для kind=use
    заполняется master_id; work_id/session_id (nullable) связывают списание
    с тату-работой/сеансом — база для будущей себестоимости сеанса.
    """

    __tablename__ = "supply_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), nullable=False, index=True)
    supply_id: Mapped[int] = mapped_column(ForeignKey("supplies.id"), nullable=False, index=True)
    delta: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    master_id: Mapped[int | None] = mapped_column(ForeignKey("masters.id"), nullable=True)
    # ondelete=SET NULL: удаление работы/сеанса не должно ломать историю
    # движений — списание уже случилось, теряется только ссылка
    work_id: Mapped[int | None] = mapped_column(
        ForeignKey("tattoo_works.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("tattoo_sessions.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    supply: Mapped["Supply"] = relationship(back_populates="movements")
    master: Mapped["Master | None"] = relationship()


class TattooFileKind(str, enum.Enum):
    sketch = "sketch"  # эскиз
    result = "result"  # фото результата


class TattooFile(Base):
    """Фото сеанса (эскиз/результат), лежит на диске в UPLOAD_DIR.

    В БД — только метаданные и относительный путь: папка переезжает
    на другую машину (VPS) вместе с дампом БД без правок.
    """

    __tablename__ = "tattoo_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("tattoo_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[TattooFileKind] = mapped_column(Enum(TattooFileKind), nullable=False)

    # Относительный путь от settings.upload_dir; имя генерируем сами
    # (uuid), оригинальное имя пользователя храним рядом
    stored_path: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    original_name: Mapped[str] = mapped_column(String(256), nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped["TattooSession"] = relationship(back_populates="files")
