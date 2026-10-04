"""Расчёт рекомендуемой цены тату-работы по прайсу студии.

Формула (премиум-сегмент, Алматы):

    Цена = max(База, Ставка размера × K_стиль × K_зона × K_доп) × K_глоб / 100

Считать по см² в премиуме нельзя: микро-реализм на ребре займёт 5 часов,
а большой контурный трайбл на бедре — 2. Поэтому — ставка размера ×
коэффициенты стиля/зоны/спец-задач.

Владелец калибрует все коэффициенты в разделе «Бизнес» (businesses.
pricing_config); здесь — значения по умолчанию. Свои значения замещают
дефолты по ключам (merge по секциям).

Точка входа — estimate_price(cfg, ...): её вызывает
GET /api/tattoo/price/estimate.
"""

# --- Ставки размера (по самой длинной стороне) ------------------------------
# Опущены на ~12–15% от стартовой сетки по решению владельца.
# XS (до 5×5): минимальный вызов мастера / гигиенический сетапер
RATE_XS = 22_000
# S (5×5 — 10×10)
RATE_S = 35_000
# M (10×10 — 15×20)
RATE_M = 60_000
# L (>20×20 / крупный проект): нижняя граница дневного сеанса —
# итог уточняется по проекту на консультации
RATE_L = 130_000

# --- Коэффициенты стилей (K_стиль) -------------------------------------------
# 1.0 — лёгкие, 1.3 — средние, 1.6 — сложные (см. прайс-таблицу студии)
STYLE_FACTOR_DEFAULTS = {
    "Минимализм (Minimalism)": 1.0,
    "Лайнворк (Linework)": 1.0,
    "Леттеринг (Lettering)": 1.0,
    "Хэндпоук (Handpoke)": 1.0,
    "Традиционный (Traditional / Old School)": 1.3,
    "Нью-скул (New School)": 1.3,
    "Геометрия (Geometry)": 1.3,
    "Акварель (Watercolor)": 1.3,
    "Орнаментал (Ornamental)": 1.3,
    "Дотворк (Dotwork)": 1.3,
    "Трайбл (Tribal)": 1.3,
    "Нео-традишнл (Neo-Traditional)": 1.3,
    "Чикано (Chicano)": 1.3,
    "Эскизный / Скетч стайл (Sketch style)": 1.3,
    "Реализм (Realism)": 1.6,
    "Блэкворк (Blackwork)": 1.6,
    "Графика / Гравюра (Engraving / Woodcut)": 1.6,
    "Японский (Irezumi)": 1.6,
    "Киберпанк / Трэш-полька (Cyberpunk / Trash Polka)": 1.6,
    "Биомеханика / Биоорганика (Biomechanics / Bioorganic)": 1.6,
}
# Стиль без коэффициента (не в конфиге) — средний, безопасная оценка для счёта
STYLE_FACTOR_FALLBACK = 1.3

# --- Коэффициенты зон (K_зона), по уровням сложности -------------------------
# «std» — стандарт: предплечье, плечо, икры, переднее бедро, спина…
# «elevated» — повышенная: тонкая кожа / трение / болезненность
# «critical» — критическая: внутр. бедро, пальцы/ладони, подмышки, голова
ZONE_FACTORS_DEFAULTS = {"std": 1.0, "elevated": 1.2, "critical": 1.4}

# Распределение зон справочника по уровням (не указанные → «std»)
ZONE_TIER = {
    "Внутренняя поверхность бедра": "critical",
    "Кисть, пальцы и ладони": "critical",
    "Подмышечная впадина": "critical",
    "Голова и заушная зона": "critical",
    "Ребра и бока": "elevated",
    "Шея и горло": "elevated",
    "Живот и паховая зона": "elevated",
    "Колено и подколенная ямка": "elevated",
    "Запястье и сгиб локтя": "elevated",
    "Локоть": "elevated",
    "Грудь и ключицы": "elevated",
    "Стопа и подъем ноги": "elevated",
}

# --- Коэффициент спец-задач (K_доп) -------------------------------------------
# Перекрытие (cover-up) / работа по шрамам: плотный проход + подгонка эскиза
# (вилка прайса 1.3–1.5, берём середину)
COVERUP_FACTOR_DEFAULT = 1.4

# --- Глобальный коэффициент (%): 100 = как в конфиге ниже --------------------
GLOBAL_PERCENT_DEFAULT = 100.0

# --- Предоплата брони (%) -----------------------------------------------------
# Счёт на столько процентов от договорной цены выставляется автоматически,
# когда мастер подтверждает работу (принимает оффер). 0 = без предоплаты.
PREPAY_PERCENT_DEFAULT = 30.0

# Округление итога до тысяч (89 600 → 90 000)
ROUND_TO = 1000

# Полный конфиг по умолчанию (то, что видит владелец, пока не правил прайс)
DEFAULT_PRICING = {
    "global_percent": GLOBAL_PERCENT_DEFAULT,
    "prepay_percent": PREPAY_PERCENT_DEFAULT,
    "size_rates": {"xs": RATE_XS, "s": RATE_S, "m": RATE_M, "l": RATE_L},
    "style_factors": dict(STYLE_FACTOR_DEFAULTS),
    "zone_factors": dict(ZONE_FACTORS_DEFAULTS),
    "coverup_factor": COVERUP_FACTOR_DEFAULT,
}


def effective_pricing(business) -> dict:
    """Конфиг прайса бизнеса: его значения из БД поверх дефолтов.

    Секции мержатся по ключам, чтобы правка одной ставки не требовала
    хранить весь конфиг целиком.
    """
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_PRICING.items()}
    stored = getattr(business, "pricing_config", None) if business else None
    if isinstance(stored, dict):
        for key, value in stored.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


def _size_rate(cfg: dict, length_cm: float, height_cm: float) -> float:
    """Ставка размера — по самой длинной стороне тату."""
    longest = max(float(length_cm), float(height_cm))
    rates = cfg["size_rates"]
    if longest <= 5:
        return rates["xs"]
    if longest <= 10:
        return rates["s"]
    if longest <= 20:
        return rates["m"]
    return rates["l"]


def _zone_factor(cfg: dict, placement: str | None) -> float:
    tier = ZONE_TIER.get(placement or "", "std")
    return cfg["zone_factors"].get(tier, ZONE_FACTORS_DEFAULTS[tier])


def estimate_price(
    cfg: dict,
    length_cm: float,
    height_cm: float,
    style: str | None = None,
    placement: str | None = None,
    coverup: bool = False,
) -> int | None:
    """Рекомендуемая цена работы в тенге (int) или None, если параметров
    недостаточно.

    cfg — конфиг из effective_pricing(business).
    """
    if not length_cm or not height_cm:
        return None

    style_factor = (
        cfg["style_factors"].get(style, STYLE_FACTOR_FALLBACK)
        if style
        else STYLE_FACTOR_FALLBACK
    )
    rate = _size_rate(cfg, length_cm, height_cm) * style_factor * _zone_factor(cfg, placement)
    if coverup:
        rate *= cfg.get("coverup_factor", COVERUP_FACTOR_DEFAULT)

    price = max(cfg["size_rates"]["xs"], rate) * cfg.get(
        "global_percent", GLOBAL_PERCENT_DEFAULT
    ) / 100
    return int(round(price / ROUND_TO) * ROUND_TO)