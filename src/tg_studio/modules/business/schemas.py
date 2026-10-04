from pydantic import BaseModel, Field, field_validator


class BusinessResponse(BaseModel):
    id: int
    name: str
    description: str | None
    phone: str | None
    is_active: bool


class AdminBusinessResponse(BusinessResponse):
    """Профиль бизнеса для панели владельца (с телеграм-id владельца)."""

    owner_telegram_id: int | None
    # Действующий прайс-конфиг (значения из БД поверх дефолтов pricing.py)
    pricing_config: dict | None = None


class PricingConfigUpdate(BaseModel):
    """Правка прайса владельцем в разделе «Бизнес». Отправляется целиком —
    так видны все значения, без частичных патчей по коэффициенту."""

    global_percent: float = Field(100, ge=10, le=500, description="100 = базовый прайс")
    # Процент предоплаты брони: автосчёт при подтверждении работы мастером
    prepay_percent: float = Field(30, ge=0, le=100, description="0 = без предоплаты")
    size_rates: dict[str, float]
    style_factors: dict[str, float]
    zone_factors: dict[str, float]
    coverup_factor: float = Field(1.4, ge=0.1, le=10)

    @field_validator("size_rates")
    @classmethod
    def check_size_rates(cls, v: dict[str, float]) -> dict[str, float]:
        keys = {"xs", "s", "m", "l"}
        if set(v) != keys:
            raise ValueError("size_rates: нужны ключи xs, s, m, l")
        if any(x <= 0 for x in v.values()):
            raise ValueError("size_rates: ставки должны быть положительными")
        return v

    @field_validator("style_factors")
    @classmethod
    def check_style_factors(cls, v: dict[str, float]) -> dict[str, float]:
        from tg_studio.modules.tattoo.schemas import TattooStyle

        valid = {s.value for s in TattooStyle}
        unknown = set(v) - valid
        if unknown:
            raise ValueError(f"Неизвестные стили: {', '.join(sorted(unknown))}")
        if any(x < 0.1 or x > 10 for x in v.values()):
            raise ValueError("Коэффициенты стилей — от 0.1 до 10")
        return v

    @field_validator("zone_factors")
    @classmethod
    def check_zone_factors(cls, v: dict[str, float]) -> dict[str, float]:
        keys = {"std", "elevated", "critical"}
        if set(v) != keys:
            raise ValueError("zone_factors: нужны ключи std, elevated, critical")
        if any(x < 0.1 or x > 10 for x in v.values()):
            raise ValueError("Коэффициенты зон — от 0.1 до 10")
        return v


class MasterCreate(BaseModel):
    full_name: str
    description: str | None = None
    telegram_id: int | None = None
    default_duration_minutes: int | None = None
    # Стили мастера (строки справочника TattooStyle). Пусто = универсал.
    specializations: list[str] = []


class MasterUpdate(BaseModel):
    full_name: str | None = None
    description: str | None = None
    telegram_id: int | None = None
    is_active: bool | None = None
    default_duration_minutes: int | None = None
    specializations: list[str] | None = None


class MasterResponse(BaseModel):
    id: int
    full_name: str
    description: str | None
    telegram_id: int | None
    is_active: bool
    google_calendar_id: str | None = None
    default_duration_minutes: int | None = None
    specializations: list[str] = []
    # прямая ссылка «добавить календарь в мой Google Calendar» (без письма)
    calendar_add_url: str | None = None
