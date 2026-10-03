import enum
import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Справочники: стили и зоны нанесения.
# Хранятся в БД строками, валидируются Pydantic-enum'ом на входе —
# пополнить список = правка здесь, миграция не нужна.
# ---------------------------------------------------------------------------


class TattooStyle(str, enum.Enum):
    traditional = "Традиционный (Traditional / Old School)"
    new_school = "Нью-скул (New School)"
    realism = "Реализм (Realism)"
    blackwork = "Блэкворк (Blackwork)"
    dotwork = "Дотворк (Dotwork)"
    engraving = "Графика / Гравюра (Engraving / Woodcut)"
    linework = "Лайнворк (Linework)"
    irezumi = "Японский (Irezumi)"
    watercolor = "Акварель (Watercolor)"
    tribal = "Трайбл (Tribal)"
    minimalism = "Минимализм (Minimalism)"
    neo_traditional = "Нео-традишнл (Neo-Traditional)"
    chicano = "Чикано (Chicano)"
    biomech = "Биомеханика / Биоорганика (Biomechanics / Bioorganic)"
    handpoke = "Хэндпоук (Handpoke)"
    geometry = "Геометрия (Geometry)"
    ornamental = "Орнаментал (Ornamental)"
    lettering = "Леттеринг (Lettering)"
    cyberpunk_trash_polka = "Киберпанк / Трэш-полька (Cyberpunk / Trash Polka)"
    sketch = "Эскизный / Скетч стайл (Sketch style)"


class TattooPlacement(str, enum.Enum):
    forearm = "Предплечье (внутренняя / внешняя сторона)"
    shoulder_biceps = "Плечо и бицепс"
    hand_fingers = "Кисть, пальцы и ладони"
    wrist_elbow_bend = "Запястье и сгиб локтя"
    elbow = "Локоть"
    neck_throat = "Шея и горло"
    head_behind_ear = "Голова и заушная зона"
    chest_collarbones = "Грудь и ключицы"
    ribs_sides = "Ребра и бока"
    belly_groin = "Живот и паховая зона"
    back_full = "Спина (вся площадь)"
    lower_back_sacrum = "Поясница и крестец"
    thigh_inner = "Внутренняя поверхность бедра"
    thigh_front_outer = "Передняя и внешняя поверхность бедра"
    buttocks = "Ягодицы и подягодичная складка"
    knee = "Колено и подколенная ямка"
    shin_calf = "Голень и икра"
    ankle = "Щиколотка и лодыжка"
    armpit = "Подмышечная впадина"
    foot_instep = "Стопа и подъем ноги"


# Зоны с повышенной сложностью (трение, тонкая кожа, болезненность и т.п.)
DIFFICULT_PLACEMENTS = frozenset(
    {
        TattooPlacement.hand_fingers,
        TattooPlacement.wrist_elbow_bend,
        TattooPlacement.elbow,
        TattooPlacement.neck_throat,
        TattooPlacement.ribs_sides,
        TattooPlacement.belly_groin,
        TattooPlacement.thigh_inner,
        TattooPlacement.knee,
        TattooPlacement.ankle,
        TattooPlacement.armpit,
        TattooPlacement.foot_instep,
    }
)

COMPLEXITY_OPTIONS = ["низкая", "средняя", "высокая"]


def normalize_phone(raw: str) -> str | None:
    """Телефон в E.164 (+77011234567). None — если цифр нет.

    Казахстан/Россия: «8 701 123-45-67» → «+77011234567».
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return f"+{digits}"


class TattooClientOut(BaseModel):
    """Клиент для пикера при создании работы."""

    model_config = {"from_attributes": True}

    id: int
    full_name: str
    phone: str | None
    username: str | None
    instagram_username: str | None
    note: str | None = None


class TattooClientCreate(BaseModel):
    """Ручное создание клиента (клиент пришёл не из Telegram).

    Дедуп по телефону/инсте делает эндпоинт: совпадение возвращает
    существующего клиента с дозаполнением пустых полей.
    """

    full_name: str = Field(..., min_length=1, max_length=256)
    phone: str | None = Field(None, max_length=20)
    instagram_username: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=2000)

    @field_validator("phone")
    @classmethod
    def _phone_e164(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        return normalize_phone(v)

    @field_validator("instagram_username")
    @classmethod
    def _insta_lower(cls, v: str | None) -> str | None:
        v = (v or "").strip().lstrip("@")
        return v.lower() or None


class TattooClientUpdate(BaseModel):
    """Правка карточки клиента (опечатки, дозаполнение)."""

    full_name: str | None = Field(None, min_length=1, max_length=256)
    phone: str | None = Field(None, max_length=20)
    instagram_username: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=2000)

    @field_validator("phone")
    @classmethod
    def _phone_e164(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        return normalize_phone(v)

    @field_validator("instagram_username")
    @classmethod
    def _insta_lower(cls, v: str | None) -> str | None:
        v = (v or "").strip().lstrip("@")
        return v.lower() or None


class TattooFileOut(BaseModel):
    """Фото сеанса (эскиз/результат), лежит на диске."""

    model_config = {"from_attributes": True}

    id: int
    session_id: int
    kind: str
    original_name: str
    mime: str
    size_bytes: int
    created_at: datetime
    # Подписанная ссылка (TTL час), проставляется эндпоинтом на ORM-объекте
    file_url: str | None = None


# ---------------------------------------------------------------------------
# TattooSession — один сеанс
# ---------------------------------------------------------------------------


class TattooSessionCreate(BaseModel):
    """Данные для нового сеанса (первый сеанс создаётся вместе с TattooWork)."""

    session_date: datetime = Field(..., description="Дата и время сеанса (ISO 8601)")
    # Рекомендуемая цена по прайсу (для счёта на предоплату) — на фронте
    # рассчитывается сама, но её можно править
    recommended_price: float | None = Field(None, gt=0)
    # Не обязателен: сеанс сначала планируется, фактически взято — после сеанса
    cost: float | None = Field(None, gt=0, description="За сколько договорились, тенге")
    sketch_file_id: str | None = Field(None, description="Telegram file_id эскиза")
    result_file_id: str | None = Field(None, description="Telegram file_id итогового фото")
    is_final_session: bool = Field(False, description="Этим сеансом работа закрывается")


class TattooSessionUpdate(BaseModel):
    """Обновление существующего сеанса."""

    session_date: datetime | None = None
    recommended_price: float | None = Field(None, gt=0)
    cost: float | None = Field(None, gt=0)
    sketch_file_id: str | None = None
    result_file_id: str | None = None
    is_final_session: bool | None = None


class TattooSessionResponse(BaseModel):
    """Ответ с данными сеанса."""

    id: int
    work_id: int
    session_date: datetime
    recommended_price: float | None
    cost: float | None
    status: str
    google_event_id: str | None
    sketch_file_id: str | None
    result_file_id: str | None
    is_final_session: bool
    llm_verdict: str | None
    llm_observed_size: str | None
    llm_observed_color: str | None
    llm_observed_style: str | None
    llm_notes: str | None
    alert_sent_at: datetime | None
    created_at: datetime
    updated_at: datetime
    files: list[TattooFileOut] = []

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# TattooWork — тату целиком (может состоять из нескольких сеансов)
# ---------------------------------------------------------------------------


class PriceEstimateResponse(BaseModel):
    """Рекомендуемая цена по прайсу (расчёт из параметров работы)."""

    recommended_price: int | None


class TattooWorkCreate(BaseModel):
    """Создание новой тату-работы вместе с первым сеансом."""

    # Для мастера игнорируется (работа его), для владельца обязателен
    master_id: int | None = Field(None, description="Мастер работы (обязателен для владельца)")
    client_id: int
    size_length_cm: float = Field(..., gt=0, description="Длина тату, см")
    size_height_cm: float = Field(..., gt=0, description="Высота тату, см")
    complexity: str = Field(..., description="Сложность: низкая, средняя, высокая")
    style: TattooStyle = Field(..., description="Стиль тату (из справочника)")
    placement: TattooPlacement = Field(..., description="Место нанесения (из справочника)")
    first_session: TattooSessionCreate


class TattooWorkUpdate(BaseModel):
    """Обновление полей тату-работы (без сеансов)."""

    size_length_cm: float | None = Field(None, gt=0)
    size_height_cm: float | None = Field(None, gt=0)
    complexity: str | None = None
    style: TattooStyle | None = None
    placement: TattooPlacement | None = None
    status: str | None = None


class TattooWorkResponse(BaseModel):
    """Ответ с данными тату-работы и её сеансами."""

    id: int
    client_id: int
    master_id: int
    business_id: int
    size_length_cm: float
    size_height_cm: float
    complexity: str
    style: str
    placement: str
    status: str
    created_at: datetime
    updated_at: datetime
    sessions: list[TattooSessionResponse] = []

    model_config = {"from_attributes": True}


class TattooOptionOut(BaseModel):
    value: str
    difficult: bool = False


class TattooOptionsResponse(BaseModel):
    """Справочники для формы создания работы."""

    styles: list[str]
    # Зоны с пометкой сложности — мастер видит предупреждение в селекте
    placements: list[TattooOptionOut]
    complexities: list[str]


class TattooWorkListResponse(BaseModel):
    """Список тату-работ мастера."""

    works: list[TattooWorkResponse]
    total: int
