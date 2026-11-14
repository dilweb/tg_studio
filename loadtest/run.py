"""Нагрузочный тест коворкинга: бьёт в уязвимые точки и сверяет деньги с «банком» (мок ApiPay).

Фазы:
  setup  — тестовый бизнес: владелец (регистрация + подтверждение почты), места 24/7,
           цена за час, мастера с телефонами (счета Kaspi уходят в мок);
  attack — категории 1–6 параллельно (гонки, деньги, сбои шлюза, фоновые задачи);
  load   — категория 7: смешанный поток со ступенчатым ростом числа пользователей;
  audit  — итоговая сверка по БД (только чтение) и по счетам мока.

Отчёт: REPORT_DIR/<время>/ — requests.jsonl, violations.json, findings.json,
mock_invoices.json, summary.md. Запуск — см. docker-compose.loadtest.yml.

Нарушение (violation) — нарушен инвариант или ответ 5xx/неожиданный статус.
Находка (finding) — не нарушение правил кода, но то, на что надо посмотреть
(например, деньги в банке есть, а сервис о них узнал только после ручного sync).
"""

import asyncio
import hashlib
import hmac
import json
import os
import random
import re
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import text

from tg_studio.config import settings
from tg_studio.timeutil import STUDIO_TZ

API_URL = os.environ.get("API_URL", "http://api:8000")
MOCK_URL = os.environ.get("MOCK_URL", "http://apipay-mock:9100")
REPORT_ROOT = Path(os.environ.get("REPORT_DIR", "/reports"))
ROUNDS = int(os.environ.get("ROUNDS", "10"))  # повторов каждого сценария гонки
BURST = int(os.environ.get("BURST", "10"))  # одновременных запросов в одной гонке
SEATS = int(os.environ.get("SEATS", "20"))
LOAD_STAGES = [int(x) for x in os.environ.get("LOAD_STAGES", "5,10,20,40").split(",") if x]
LOAD_STAGE_SECONDS = int(os.environ.get("LOAD_STAGE_SECONDS", "45"))
SEED = int(os.environ.get("SEED", "1"))

HOURLY_RATE = 1000  # тенге/час — целое, как требует ApiPay
STEP_MINUTES = 5  # сетка начала брони в тестовом бизнесе (близкие старты для гонок со временем)
# beat запускает expire_booking_holds раз в 5 мин (tasks/celery_app.py): hold, просроченный
# дольше двух интервалов, значит, что свип не работает
SWEEP_STALE_MINUTES = 2 * 5

C1, C2, C3, C4, C5, C6, C7 = (
    "1_double_booking",
    "2_double_money",
    "3_pay_vs_cancel",
    "4_booking_changes",
    "5_gateway_faults",
    "6_background_telegram",
    "7_load",
)
CATEGORY_TITLES = {
    C1: "Двойное бронирование",
    C2: "Двойное списание/зачисление",
    C3: "Оплата против отмены и истечения",
    C4: "Изменение брони под нагрузкой",
    C5: "Сбои шлюза Kaspi",
    C6: "Фоновые задачи и Telegram",
    C7: "Общая нагрузка",
}

S_PENDING = "ожидает_оплаты"
S_CONFIRMED = "подтверждена"


# ---------------------------------------------------------------------------
# Журнал и отчёт
# ---------------------------------------------------------------------------


def _path_key(path: str) -> str:
    return re.sub(r"/\d+", "/{id}", path)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


class Recorder:
    def __init__(self) -> None:
        self.started = datetime.now(UTC)
        self.dir = REPORT_ROOT / self.started.strftime("%Y%m%d-%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._requests = (self.dir / "requests.jsonl").open("w", encoding="utf-8")
        self.violations: list[dict] = []
        self.findings: list[dict] = []
        self.script_errors: list[dict] = []
        self.phase = "setup"
        # (phase, category, scenario, method path) → [(status, ms)]
        self.stats: dict[tuple, list[tuple[int | None, float]]] = defaultdict(list)
        self.stage_windows: dict[str, tuple[float, float]] = {}
        self.audit: list[dict] = []

    def request(self, rec: dict) -> None:
        rec["phase"] = self.phase
        self._requests.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        key = (self.phase, rec["category"], rec["scenario"], f"{rec['method']} {_path_key(rec['path'])}")
        self.stats[key].append((rec["status"], rec["ms"]))

    def violation(self, category: str, scenario: str, message: str, **details) -> None:
        self.violations.append(
            {"at": datetime.now(UTC).isoformat(), "category": category, "scenario": scenario,
             "message": message, **details}
        )

    def finding(self, category: str, scenario: str, message: str, **details) -> None:
        self.findings.append(
            {"at": datetime.now(UTC).isoformat(), "category": category, "scenario": scenario,
             "message": message, **details}
        )

    def script_error(self, category: str, scenario: str, exc: BaseException) -> None:
        self.script_errors.append(
            {"category": category, "scenario": scenario, "error": repr(exc),
             "traceback": traceback.format_exc()}
        )

    def close(self) -> None:
        self._requests.close()


@dataclass
class Resp:
    status: int | None
    body: object
    ms: float
    t_send: float
    t_recv: float

    def ok(self, *codes: int) -> bool:
        return self.status in codes


def _host_header() -> dict:
    """API за TrustedHostMiddleware (require_https) принимает только публичный Host."""
    if not settings.trusted_hosts:
        return {}
    netloc = urlsplit(settings.api_public_url or settings.public_url).netloc
    return {"Host": netloc} if netloc else {}


# ---------------------------------------------------------------------------
# Акторы и клиенты
# ---------------------------------------------------------------------------


def init_data_headers(telegram_id: int) -> dict:
    """Подписанный Telegram initData — как у миниаппа (тот же BOT_TOKEN, что у сервиса)."""
    user = json.dumps({"id": telegram_id, "first_name": "LT"}, separators=(",", ":"))
    params = {"auth_date": str(int(time.time())), "user": user}
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": f"TelegramInitData {urlencode(params)}"}


@dataclass
class Master:
    id: int
    tg_id: int
    phone: str | None
    name: str

    async def headers(self, api: "Api") -> dict:
        return init_data_headers(self.tg_id)


@dataclass
class Owner:
    email: str
    password: str
    token: str = ""
    token_at: float = 0.0
    name: str = "owner"
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def headers(self, api: "Api") -> dict:
        # access JWT живёт jwt_access_token_expire_minutes — обновляем заранее
        if time.monotonic() - self.token_at > settings.jwt_access_token_expire_minutes * 60 / 2:
            async with self._lock:
                if time.monotonic() - self.token_at > settings.jwt_access_token_expire_minutes * 60 / 2:
                    resp = await api.http.post(
                        "/api/auth/login", json={"email": self.email, "password": self.password}
                    )
                    resp.raise_for_status()
                    self.token = resp.json()["access_token"]
                    self.token_at = time.monotonic()
        return {"Authorization": f"Bearer {self.token}"}


class Api:
    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.http = httpx.AsyncClient(
            base_url=API_URL,
            timeout=120,
            # простаивающее соединение закрываем раньше uvicorn (timeout_keep_alive=5 с):
            # иначе клиент шлёт запрос в сокет, который сервер как раз закрывает (ReadError)
            limits=httpx.Limits(max_connections=2000, max_keepalive_connections=500, keepalive_expiry=2),
            headers=_host_header(),
        )

    async def call(
        self,
        actor,
        method: str,
        path: str,
        *,
        cat: str,
        sc: str,
        expect: tuple[int, ...] = (200,),
        json_body=None,
        params=None,
        headers: dict | None = None,
    ) -> Resp:
        hdrs = dict(headers or {})
        if actor is not None:
            hdrs.update(await actor.headers(self))
        t_send = time.time()
        error = None
        try:
            r = await self.http.request(method, path, json=json_body, params=params, headers=hdrs)
            status = r.status_code
            try:
                body = r.json()
            except ValueError:
                body = r.text
        except httpx.HTTPError as exc:
            status, body, error = None, None, f"{type(exc).__name__}: {exc}"
        t_recv = time.time()
        ms = (t_recv - t_send) * 1000
        ok = status in expect
        body_text = json.dumps(body, ensure_ascii=False, default=str) if body is not None else ""
        self.rec.request(
            {
                "ts": datetime.fromtimestamp(t_send, UTC).isoformat(),
                "category": cat,
                "scenario": sc,
                "actor": getattr(actor, "name", None),
                "method": method,
                "path": path,
                "params": params,
                "request": json_body,
                "status": status,
                "ms": round(ms, 1),
                "error": error,
                "response": body_text if not ok else body_text[:300],
            }
        )
        if status is None or status >= 500:
            self.rec.violation(cat, sc, f"{'нет ответа' if status is None else status} на {method} {path}",
                               request=json_body, params=params, response=body if body is not None else error)
        elif not ok:
            self.rec.violation(cat, sc, f"неожиданный статус {status} на {method} {path} (ожидали {expect})",
                               request=json_body, params=params, response=body)
        return Resp(status, body, ms, t_send, t_recv)


class Mock:
    """Управление моком ApiPay (банк)."""

    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.http = httpx.AsyncClient(base_url=MOCK_URL, timeout=120)

    async def _post(self, path: str, body: dict, cat: str, sc: str) -> dict:
        t0 = time.time()
        r = await self.http.post(path, json=body)
        self.rec.request(
            {"ts": datetime.fromtimestamp(t0, UTC).isoformat(), "category": cat, "scenario": sc,
             "actor": "bank", "method": "POST", "path": f"mock{path}", "params": None, "request": body,
             "status": r.status_code, "ms": round((time.time() - t0) * 1000, 1), "error": None,
             "response": r.text[:1000]}
        )
        r.raise_for_status()
        result = r.json()
        for hook in result.get("webhooks") or []:
            if hook.get("status") is None or hook["status"] >= 500:
                self.rec.violation(cat, sc, "вебхук ApiPay не принят сервисом (5xx/нет ответа)",
                                   order=body.get("order"), webhook=hook)
        return result

    async def behaviour(self, phone: str, **beh) -> None:
        (await self.http.put(f"/_control/behaviour/{phone}", json=beh)).raise_for_status()

    async def pay(self, order: str, cat: str, sc: str, **opts) -> dict:
        return await self._post("/_control/pay", {"order": order, **opts}, cat, sc)

    async def webhook(self, order: str, cat: str, sc: str, times: int = 1) -> dict:
        return await self._post("/_control/webhook", {"order": order, "times": times}, cat, sc)

    async def invoices(self) -> list[dict]:
        r = await self.http.get("/_control/invoices")
        r.raise_for_status()
        return r.json()


async def burst(factories) -> list:
    """Запустить корутины одновременно (общий старт), вернуть результаты по порядку."""
    gate = asyncio.Event()

    async def run(f):
        await gate.wait()
        return await f()

    tasks = [asyncio.create_task(run(f)) for f in factories]
    await asyncio.sleep(0)
    gate.set()
    return await asyncio.gather(*tasks)


async def at(epoch: float, factory):
    """Выполнить корутину в момент epoch (секунды Unix)."""
    delay = epoch - time.time()
    if delay > 0:
        await asyncio.sleep(delay)
    return await factory()


def order_of(payment_id: int) -> str:
    """external_order_id, который сервис передаёт в ApiPay (providers.create_invoice)."""
    return f"payment-{payment_id}"


def grid_after(seconds: float) -> datetime:
    """Ближайший момент на сетке STEP_MINUTES не раньше now + seconds (студийное время)."""
    t = datetime.now(STUDIO_TZ) + timedelta(seconds=seconds)
    t = t.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return t + timedelta(minutes=(-t.minute) % STEP_MINUTES)


# ---------------------------------------------------------------------------
# Контекст теста
# ---------------------------------------------------------------------------


class Ctx:
    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.api = Api(rec)
        self.mock = Mock(rec)
        self.run_id = int(time.time())
        self.owner: Owner | None = None
        self.business_id = 0
        self.seats: list[int] = []
        self.refund_tiers: list[dict] = []
        self._slot_n = 0
        self._master_n = 0
        self._slot_base = (datetime.now(STUDIO_TZ) + timedelta(days=3)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self._near: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
        self.test_tg_ids: set[int] = set()
        # заказы, где «в банке оплачено, в сервисе нет» — ожидаемое поведение сценария
        self.expected_unapplied: dict[str, str] = {}
        # заказы, где счёт в банке остался открытым ожидаемо (отмена в банке сломана)
        self.expected_open_at_bank: dict[str, str] = {}

    # --- время и места -----------------------------------------------------

    def slot(self) -> datetime:
        """Уникальное окно в далёком будущем (+3 дня и дальше), шаг 3 часа: брони
        разных сценариев не пересекаются, даже с продлением на час."""
        self._slot_n += 1
        return self._slot_base + timedelta(hours=3 * self._slot_n)

    def near_seat(self, start: datetime, end: datetime) -> int:
        """Место, свободное от других «близких по времени» сценариев на [start, end)."""
        for seat in self.seats:
            if all(not (s < end and start < e) for s, e in self._near[seat]):
                self._near[seat].append((start, end))
                return seat
        raise RuntimeError(f"Не хватает мест на {start}–{end}: увеличьте SEATS")

    # --- действия ----------------------------------------------------------

    async def new_master(self, *, phone: bool = True, balance: int = 0, cat: str = "setup",
                         sc: str = "setup") -> Master:
        self._master_n += 1
        n = self._master_n
        tg_id = self.run_id * 1000 + n
        name = f"LT {self.run_id}-{n}"
        r = await self.api.call(self.owner, "POST", "/api/admin/masters", cat=cat, sc=sc, expect=(201,),
                                json_body={"full_name": name, "telegram_id": tg_id})
        master_id = r.body["id"]
        ph = None
        if phone:
            ph = f"87{(self.run_id % 1000) * 100_000 + n:09d}"  # 8 + 10 цифр — формат ApiPay
            await self.api.call(self.owner, "PATCH", f"/api/admin/masters/{master_id}", cat=cat, sc=sc,
                                json_body={"phone": ph})
        m = Master(master_id, tg_id, ph, name)
        self.test_tg_ids.add(tg_id)
        # первый вход мастера создаёт его User: делаем заранее, не внутри гонки
        await self.api.call(m, "GET", "/api/me/balance", cat=cat, sc=sc)
        if balance:
            await self.api.call(self.owner, "POST", f"/api/masters/{master_id}/topup", cat=cat, sc=sc,
                                expect=(201,), json_body={"amount": balance, "note": "loadtest"})
        return m

    async def book(self, m: Master, seat: int, start: datetime, hours: int = 1, *, cat: str, sc: str,
                   expect=(201,)) -> Resp:
        return await self.api.call(
            m, "POST", "/api/bookings", cat=cat, sc=sc, expect=expect,
            json_body={"seat_id": seat, "start": start.isoformat(),
                       "end": (start + timedelta(hours=hours)).isoformat()},
        )

    async def pay_balance(self, m: Master, booking_id: int, *, cat: str, sc: str, expect=(200,)) -> Resp:
        return await self.api.call(m, "POST", f"/api/bookings/{booking_id}/pay", cat=cat, sc=sc,
                                   expect=expect, json_body={"method": "balance"})

    async def cancel(self, actor, booking_id: int, *, cat: str, sc: str, expect=(200,)) -> Resp:
        return await self.api.call(actor, "POST", f"/api/bookings/{booking_id}/cancel", cat=cat, sc=sc,
                                   expect=expect, json_body={"reason": "loadtest"})

    async def booking(self, actor, booking_id: int, *, cat: str, sc: str) -> dict:
        return (await self.api.call(actor, "GET", f"/api/bookings/{booking_id}", cat=cat, sc=sc)).body

    async def history(self, actor, booking_id: int, *, cat: str, sc: str) -> list[dict]:
        r = await self.api.call(actor, "GET", f"/api/bookings/{booking_id}/history", cat=cat, sc=sc)
        return r.body["events"]

    async def balance(self, m: Master, *, cat: str, sc: str) -> float:
        return (await self.api.call(m, "GET", "/api/me/balance", cat=cat, sc=sc)).body["balance"]

    async def sync(self, actor, payment_id: int, *, cat: str, sc: str, expect=(200,)) -> Resp:
        return await self.api.call(actor, "POST", f"/api/payments/{payment_id}/sync", cat=cat, sc=sc,
                                   expect=expect)

    # --- проверки ----------------------------------------------------------

    def exactly_one(self, cat: str, sc: str, results: list[Resp], codes: tuple[int, ...], what: str,
                    **details) -> list[int]:
        winners = [i for i, r in enumerate(results) if r.status in codes]
        if len(winners) != 1:
            self.rec.violation(cat, sc, f"{what}: успешных {len(winners)}, ожидалась ровно 1",
                               statuses=[r.status for r in results], **details)
        return winners

    def card_payment(self, cat: str, sc: str, booking: Resp) -> dict | None:
        """Счёт брони должен уйти в Kaspi (мок): мастер с телефоном, ApiPay настроен."""
        payment = (booking.body or {}).get("payment") if isinstance(booking.body, dict) else None
        if not payment or payment.get("provider") != "apipay":
            self.rec.violation(cat, sc, "счёт брони не ушёл в Kaspi (provider != apipay)",
                               booking=booking.body)
            return None
        return payment

    async def guard(self, cat: str, sc: str, coro) -> None:
        try:
            await coro
        except Exception as exc:  # сбой самого скрипта — в отчёт отдельно, тест продолжается
            self.rec.script_error(cat, sc, exc)


# ---------------------------------------------------------------------------
# Подготовка тестового бизнеса
# ---------------------------------------------------------------------------


async def setup(ctx: Ctx) -> None:
    from tg_studio.api.auth import create_email_verification_token

    api, cat, sc = ctx.api, "setup", "setup"
    email = f"loadtest+{ctx.run_id}@example.com"
    password = f"lt-{ctx.run_id}-pass"
    r = await api.call(None, "POST", "/api/auth/register/owner", cat=cat, sc=sc, expect=(201,),
                       json_body={"email": email, "password": password, "first_name": "Loadtest",
                                  "business_name": f"Loadtest {ctx.run_id}"})
    if r.status != 201:
        raise RuntimeError(f"Регистрация владельца не удалась: {r.status} {r.body}")
    owner_user_id = r.body["user_id"]
    # почта подтверждается той же ссылкой, что ушла бы письмом (секрет JWT — из .env)
    await api.call(None, "GET", "/api/auth/verify-email", cat=cat, sc=sc,
                   params={"token": create_email_verification_token(owner_user_id)})
    ctx.owner = Owner(email, password)
    owner = ctx.owner

    biz = await api.call(owner, "GET", "/api/admin/business", cat=cat, sc=sc)
    ctx.business_id = biz.body["id"]
    await api.call(owner, "PATCH", "/api/coworking/settings", cat=cat, sc=sc,
                   json_body={"max_seats": max(SEATS, 4), "step_minutes": STEP_MINUTES,
                              "min_duration_minutes": 15})
    # студия открыта круглосуточно: гонки на границах ступеней возврата идут в любое время запуска
    await api.call(owner, "PUT", "/api/studio-hours", cat=cat, sc=sc,
                   json_body={"entries": [{"weekday": d, "open_time": "00:00", "close_time": "00:00"}
                                          for d in range(7)]})
    await api.call(owner, "POST", "/api/price-rules", cat=cat, sc=sc, expect=(201,),
                   json_body={"name": "Loadtest", "time_from": "00:00", "time_to": "00:00",
                              "hourly_rate": HOURLY_RATE})
    seats = (await api.call(owner, "GET", "/api/seats", cat=cat, sc=sc)).body["seats"]
    for i in range(len(seats), SEATS):
        await api.call(owner, "POST", "/api/seats", cat=cat, sc=sc, expect=(201,),
                       json_body={"name": f"LT {i + 1}"})
    seats = (await api.call(owner, "GET", "/api/seats", cat=cat, sc=sc)).body["seats"]
    ctx.seats = [s["id"] for s in seats if s.get("is_active", True)][:SEATS]
    cfg = (await api.call(owner, "GET", "/api/coworking/settings", cat=cat, sc=sc)).body
    ctx.refund_tiers = sorted(cfg["refund_tiers"], key=lambda t: t["hours_before"], reverse=True)
    if not settings.telegram_webhook_secret:
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET не задан — см. docker-compose.loadtest.yml")


# ---------------------------------------------------------------------------
# 1. Двойное бронирование
# ---------------------------------------------------------------------------


async def c1_double_booking(ctx: Ctx) -> None:
    cat = C1
    pool = [await ctx.new_master(cat=cat) for _ in range(BURST)]
    a, b = pool[0], pool[1]
    seat0 = ctx.seats[0]

    async def same_seat():
        sc, s = "same_seat_same_time", ctx.slot()
        res = await burst([lambda m=m: ctx.book(m, seat0, s, cat=cat, sc=sc, expect=(201, 409)) for m in pool])
        ctx.exactly_one(cat, sc, res, (201,), "бронь одного места на одно время", start=s.isoformat())

    async def same_master():
        sc, s = "same_master_many_seats", ctx.slot()
        seats = ctx.seats[:BURST]
        res = await burst([lambda st=st: ctx.book(a, st, s, cat=cat, sc=sc, expect=(201, 409)) for st in seats])
        ctx.exactly_one(cat, sc, res, (201,), "мастер на разных местах в одно время", start=s.isoformat())

    async def partial_overlap():
        sc, s = "partial_overlap", ctx.slot()
        res = await burst([
            lambda: ctx.book(a, seat0, s, 2, cat=cat, sc=sc, expect=(201, 409)),
            lambda: ctx.book(b, seat0, s + timedelta(hours=1), 1, cat=cat, sc=sc, expect=(201, 409)),
        ])
        ctx.exactly_one(cat, sc, res, (201,), "частично пересекающиеся брони", start=s.isoformat())

    async def reschedule_into_taken():
        sc, s1, s2 = "reschedule_vs_create", ctx.slot(), ctx.slot()
        own = await ctx.book(a, seat0, s1, cat=cat, sc=sc)
        if own.status != 201:
            return
        res = await burst([
            lambda: ctx.api.call(a, "PATCH", f"/api/bookings/{own.body['id']}", cat=cat, sc=sc,
                                 expect=(200, 409), json_body={"start": s2.isoformat(),
                                                               "end": (s2 + timedelta(hours=1)).isoformat()}),
            lambda: ctx.book(b, seat0, s2, cat=cat, sc=sc, expect=(201, 409)),
        ])
        ctx.exactly_one(cat, sc, res, (200, 201), "перенос и новая бронь в один слот", booking=own.body["id"])

    async def extend_into_taken():
        sc, s = "extend_vs_create", ctx.slot()
        own = await ctx.book(a, seat0, s, cat=cat, sc=sc)
        if own.status != 201:
            return
        res = await burst([
            lambda: ctx.api.call(a, "POST", f"/api/bookings/{own.body['id']}/extend", cat=cat, sc=sc,
                                 expect=(200, 409), json_body={"hours": 1}),
            lambda: ctx.book(b, seat0, s + timedelta(hours=1), cat=cat, sc=sc, expect=(201, 409)),
        ])
        ctx.exactly_one(cat, sc, res, (200, 201), "продление и новая бронь в один слот", booking=own.body["id"])

    for _ in range(ROUNDS):
        for name, fn in (("same_seat_same_time", same_seat), ("same_master_many_seats", same_master),
                         ("partial_overlap", partial_overlap), ("reschedule_vs_create", reschedule_into_taken),
                         ("extend_vs_create", extend_into_taken)):
            await ctx.guard(cat, name, fn())


# ---------------------------------------------------------------------------
# 2. Двойное списание и зачисление
# ---------------------------------------------------------------------------


async def c2_double_money(ctx: Ctx) -> None:
    cat = C2
    seat0 = ctx.seats[1]
    price = HOURLY_RATE

    async def balance_for_two():
        sc = "balance_enough_for_one"
        m = await ctx.new_master(balance=price, cat=cat, sc=sc)
        b1 = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        b2 = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        if b1.status != 201 or b2.status != 201:
            return
        ids = [b1.body["id"], b1.body["id"], b2.body["id"], b2.body["id"]]
        res = await burst([lambda i=i: ctx.pay_balance(m, i, cat=cat, sc=sc, expect=(200, 409)) for i in ids])
        ctx.exactly_one(cat, sc, res, (200,), "оплата двух броней при балансе на одну", master=m.id)
        bal = await ctx.balance(m, cat=cat, sc=sc)
        if bal != 0:
            ctx.rec.violation(cat, sc, f"баланс после оплаты {bal}, ожидался 0", master=m.id)

    async def same_booking_many_pays():
        sc = "same_booking_parallel_pay"
        m = await ctx.new_master(balance=5 * price, cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        if bk.status != 201:
            return
        res = await burst([lambda: ctx.pay_balance(m, bk.body["id"], cat=cat, sc=sc, expect=(200, 409))
                           for _ in range(BURST)])
        ctx.exactly_one(cat, sc, res, (200,), "параллельная оплата одной брони", booking=bk.body["id"])
        bal = await ctx.balance(m, cat=cat, sc=sc)
        if bal != 4 * price:
            ctx.rec.violation(cat, sc, f"баланс {bal}, ожидался {4 * price} (списали не один раз?)",
                              booking=bk.body["id"])

    async def mark_paid_twice():
        sc = "manual_mark_paid_parallel"
        m = await ctx.new_master(phone=False, cat=cat, sc=sc)  # без телефона — ручной счёт
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        if bk.status != 201:
            return
        pid = bk.body["payment"]["id"]
        res = await burst([lambda: ctx.api.call(ctx.owner, "POST", f"/api/payments/{pid}/mark-paid", cat=cat,
                                                sc=sc, expect=(200, 409)) for _ in range(BURST)])
        ctx.exactly_one(cat, sc, res, (200,), "параллельное «Оплачено» администратором", payment=pid)
        state = await ctx.booking(m, bk.body["id"], cat=cat, sc=sc)
        if state["status"] != S_CONFIRMED:
            ctx.rec.violation(cat, sc, f"после оплаты бронь в статусе {state['status']}", booking=bk.body["id"])

    async def webhook_storm():
        sc = "webhook_duplicates"
        m = await ctx.new_master(cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        pay = ctx.card_payment(cat, sc, bk) if bk.status == 201 else None
        if pay is None:
            return
        await ctx.mock.pay(order_of(pay["id"]), cat, sc, webhooks=BURST)
        state = await ctx.booking(m, bk.body["id"], cat=cat, sc=sc)
        if state["status"] != S_CONFIRMED:
            ctx.rec.violation(cat, sc, f"после оплаты в банке бронь в статусе {state['status']}",
                              booking=bk.body["id"])

    async def webhook_and_sync():
        sc = "webhook_plus_sync"
        m = await ctx.new_master(cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        pay = ctx.card_payment(cat, sc, bk) if bk.status == 201 else None
        if pay is None:
            return
        order = order_of(pay["id"])
        await ctx.mock.pay(order, cat, sc, deliver=False)
        await burst([lambda: ctx.mock.webhook(order, cat, sc, times=3)]
                    + [lambda: ctx.sync(m, pay["id"], cat=cat, sc=sc) for _ in range(3)])
        state = await ctx.booking(m, bk.body["id"], cat=cat, sc=sc)
        if state["status"] != S_CONFIRMED:
            ctx.rec.violation(cat, sc, f"после оплаты в банке бронь в статусе {state['status']}",
                              booking=bk.body["id"])

    for _ in range(ROUNDS):
        for name, fn in (("balance_enough_for_one", balance_for_two),
                         ("same_booking_parallel_pay", same_booking_many_pays),
                         ("manual_mark_paid_parallel", mark_paid_twice),
                         ("webhook_duplicates", webhook_storm), ("webhook_plus_sync", webhook_and_sync)):
            await ctx.guard(cat, name, fn())


# ---------------------------------------------------------------------------
# 3. Оплата против отмены и истечения hold
# ---------------------------------------------------------------------------


async def run_sweepers(until: float, workers: int = 3) -> int:
    """Свип просроченных hold'ов параллельно beat'у (тот же код, своя сессия)."""
    from tg_studio.db.session import async_session_factory
    from tg_studio.modules.coworking.service import expire_booking_holds

    total = 0

    async def loop():
        nonlocal total
        while time.time() < until:
            async with async_session_factory() as session:
                total += await expire_booking_holds(session)
            await asyncio.sleep(0.2)

    await asyncio.gather(*(loop() for _ in range(workers)))
    return total


async def c3_pay_vs_cancel(ctx: Ctx) -> None:
    cat = C3
    seat0 = ctx.seats[2]
    price = HOURLY_RATE

    async def pay_vs_cancel_booking():
        sc = "bank_pay_vs_cancel_booking"
        m = await ctx.new_master(cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        pay = ctx.card_payment(cat, sc, bk) if bk.status == 201 else None
        if pay is None:
            return
        await burst([lambda: ctx.mock.pay(order_of(pay["id"]), cat, sc),
                     lambda: ctx.cancel(m, bk.body["id"], cat=cat, sc=sc, expect=(200, 409))])

    async def pay_vs_cancel_invoice():
        sc = "bank_pay_vs_cancel_invoice"
        m = await ctx.new_master(cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        pay = ctx.card_payment(cat, sc, bk) if bk.status == 201 else None
        if pay is None:
            return
        await burst([lambda: ctx.mock.pay(order_of(pay["id"]), cat, sc),
                     lambda: ctx.api.call(m, "POST", f"/api/payments/{pay['id']}/cancel", cat=cat, sc=sc,
                                          expect=(200, 409), json_body={"note": "loadtest"})])

    async def card_vs_balance():
        sc = "bank_pay_vs_balance_pay"
        m = await ctx.new_master(balance=price, cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        pay = ctx.card_payment(cat, sc, bk) if bk.status == 201 else None
        if pay is None:
            return
        await burst([lambda: ctx.mock.pay(order_of(pay["id"]), cat, sc),
                     lambda: ctx.pay_balance(m, bk.body["id"], cat=cat, sc=sc, expect=(200, 409))])
        # деньги пришли дважды (карта + баланс), бронь оплачена один раз → на балансе ровно цена
        bal = await ctx.balance(m, cat=cat, sc=sc)
        state = await ctx.booking(m, bk.body["id"], cat=cat, sc=sc)
        if state["status"] != S_CONFIRMED or bal != price:
            ctx.rec.violation(cat, sc, f"карта+баланс: бронь {state['status']}, баланс {bal} (ожидали "
                                       f"{S_CONFIRMED} и {price})", booking=bk.body["id"])

    async def pay_at_hold_deadline():
        """N броней с hold до старта (через ~4 мин): оплаты в банке разбросаны на ±1.5 с вокруг
        дедлайна, одновременно 3 свипа. Итог — подтверждена или истекла (деньги на балансе)."""
        sc = "bank_pay_at_hold_deadline"
        n = max(1, min(ROUNDS, SEATS - 2))
        masters = [await ctx.new_master(cat=cat, sc=sc) for _ in range(n)]
        start = grid_after(4 * 60)
        made = []
        for m in masters:
            seat = ctx.near_seat(start, start + timedelta(hours=1))
            bk = await ctx.book(m, seat, start, cat=cat, sc=sc)
            pay = ctx.card_payment(cat, sc, bk) if bk.status == 201 else None
            if pay is not None:
                made.append((m, bk.body, pay))
        if not made:
            return
        deadline = datetime.fromisoformat(made[0][1]["hold_deadline"]).timestamp()
        offsets = [(-1.5 + 3 * i / max(1, len(made) - 1)) for i in range(len(made))]
        await asyncio.gather(
            run_sweepers(deadline + 5),
            *(at(deadline + off, lambda p=p: ctx.mock.pay(order_of(p["id"]), cat, sc))
              for off, (_, _, p) in zip(offsets, made, strict=True)),
        )
        for m, bk, _ in made:
            state = await ctx.booking(m, bk["id"], cat=cat, sc=sc)
            if state["status"] not in (S_CONFIRMED, "истекла_оплата"):
                ctx.rec.violation(cat, sc, f"после дедлайна и свипа бронь в статусе {state['status']}",
                                  booking=bk["id"])

    await ctx.guard(cat, "bank_pay_at_hold_deadline", pay_at_hold_deadline())
    for _ in range(ROUNDS):
        for name, fn in (("bank_pay_vs_cancel_booking", pay_vs_cancel_booking),
                         ("bank_pay_vs_cancel_invoice", pay_vs_cancel_invoice),
                         ("bank_pay_vs_balance_pay", card_vs_balance)):
            await ctx.guard(cat, name, fn())


# ---------------------------------------------------------------------------
# 4. Изменение брони под нагрузкой
# ---------------------------------------------------------------------------


def expected_refund_pct(tiers: list[dict], hours_left: float) -> int:
    """Та же формула, что money.cancel_refund_amount (ступени от большей к меньшей)."""
    for tier in tiers:
        if hours_left >= tier["hours_before"]:
            return tier["percent"]
    return 0


async def c4_booking_changes(ctx: Ctx) -> None:
    cat = C4
    seat0 = ctx.seats[3]
    price = HOURLY_RATE

    async def triple_race():
        sc = "reschedule_cancel_pay_together"
        m = await ctx.new_master(balance=2 * price, cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        if bk.status != 201:
            return
        s2, bid = ctx.slot(), bk.body["id"]
        await burst([
            lambda: ctx.api.call(m, "PATCH", f"/api/bookings/{bid}", cat=cat, sc=sc, expect=(200, 409),
                                 json_body={"start": s2.isoformat(), "end": (s2 + timedelta(hours=1)).isoformat()}),
            lambda: ctx.cancel(m, bid, cat=cat, sc=sc, expect=(200, 409)),
            lambda: ctx.pay_balance(m, bid, cat=cat, sc=sc, expect=(200, 409)),
        ])
        # порядок событий и деньги брони проверяет итоговая сверка

    async def reschedule_paid():
        sc = "reschedule_paid_booking"
        m = await ctx.new_master(balance=price, cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        if bk.status != 201 or (await ctx.pay_balance(m, bk.body["id"], cat=cat, sc=sc)).status != 200:
            return
        s2 = ctx.slot()
        await ctx.api.call(m, "PATCH", f"/api/bookings/{bk.body['id']}", cat=cat, sc=sc, expect=(409,),
                           json_body={"start": s2.isoformat(), "end": (s2 + timedelta(hours=1)).isoformat()})

    async def refund_boundary(hours_before: int):
        """Отмены оплаченных броней разбросаны на ±2 с вокруг границы ступени: возврат
        должен совпасть с процентом по времени запроса (если отправка и ответ по разные
        стороны границы — допустимы оба)."""
        sc = f"cancel_at_{hours_before}h_boundary"
        n = max(1, min(5, ROUNDS))
        masters = [await ctx.new_master(balance=price, cat=cat, sc=sc) for _ in range(n)]
        start = grid_after(hours_before * 3600 + 4 * 60)
        made = []
        for m in masters:
            seat = ctx.near_seat(start, start + timedelta(hours=1))
            bk = await ctx.book(m, seat, start, cat=cat, sc=sc)
            if bk.status == 201 and (await ctx.pay_balance(m, bk.body["id"], cat=cat, sc=sc)).status == 200:
                made.append((m, bk.body["id"]))
        boundary = start.timestamp() - hours_before * 3600
        offsets = [(-2 + 4 * i / max(1, len(made) - 1)) for i in range(len(made))]
        results = await asyncio.gather(
            *(at(boundary + off, lambda m=m, bid=bid: ctx.cancel(m, bid, cat=cat, sc=sc))
              for off, (m, bid) in zip(offsets, made, strict=True))
        )
        for (m, bid), r in zip(made, results, strict=True):
            if r.status != 200:
                continue
            events = await ctx.history(m, bid, cat=cat, sc=sc)
            refund = sum(float((e.get("details") or {}).get("amount", 0)) for e in events if e["kind"] == "возвращена")
            allowed = {
                round(price * expected_refund_pct(ctx.refund_tiers, (start.timestamp() - t) / 3600) / 100, 2)
                for t in (r.t_send, r.t_recv)
            }
            if round(refund, 2) not in allowed:
                ctx.rec.violation(cat, sc, f"возврат {refund}, по ступеням ожидали {sorted(allowed)}",
                                  booking=bid, start=start.isoformat(), tiers=ctx.refund_tiers,
                                  sent=datetime.fromtimestamp(r.t_send, UTC).isoformat())

    async def cancel_after_start():
        sc = "cancel_after_start"
        m = await ctx.new_master(balance=price, cat=cat, sc=sc)
        start = grid_after(3 * 60)
        bk = await ctx.book(m, ctx.near_seat(start, start + timedelta(hours=1)), start, cat=cat, sc=sc)
        if bk.status != 201 or (await ctx.pay_balance(m, bk.body["id"], cat=cat, sc=sc)).status != 200:
            return
        await at(start.timestamp() + 2, lambda: ctx.cancel(m, bk.body["id"], cat=cat, sc=sc, expect=(409,)))
        events = await ctx.history(m, bk.body["id"], cat=cat, sc=sc)
        if any(e["kind"] in ("возвращена", "отменена") for e in events):
            ctx.rec.violation(cat, sc, "начавшуюся бронь отменили/вернули деньги", booking=bk.body["id"])

    timed = [ctx.guard(cat, f"cancel_at_{t['hours_before']}h_boundary", refund_boundary(t["hours_before"]))
             for t in ctx.refund_tiers if t["hours_before"] > 0]
    timed.append(ctx.guard(cat, "cancel_after_start", cancel_after_start()))

    async def rounds():
        for _ in range(ROUNDS):
            await ctx.guard(cat, "reschedule_cancel_pay_together", triple_race())
            await ctx.guard(cat, "reschedule_paid_booking", reschedule_paid())

    await asyncio.gather(rounds(), *timed)


# ---------------------------------------------------------------------------
# 5. Сбои шлюза
# ---------------------------------------------------------------------------


async def c5_gateway_faults(ctx: Ctx) -> None:
    cat = C5
    seat0 = ctx.seats[4]
    price = HOURLY_RATE

    async def slow_create(delay: float, expect_provider: str, sc: str):
        """Шлюз отвечает delay секунд: бронь создаётся; параллельно другой мастер читает —
        его время ответа сравнивается в отчёте (сценарий *_probe)."""
        m = await ctx.new_master(cat=cat, sc=sc)
        probe = await ctx.new_master(cat=cat, sc=sc)
        await ctx.mock.behaviour(m.phone, create_delay=delay)
        s = ctx.slot()

        async def probing():
            end = time.time() + delay
            while time.time() < end:
                await ctx.api.call(probe, "GET", "/api/bookings/availability", cat=cat, sc=f"{sc}_probe",
                                   params={"start": s.isoformat(), "end": (s + timedelta(hours=1)).isoformat()})
                await asyncio.sleep(0.1)  # замер, а не нагрузка: ~10 запросов/с

        bk, _ = await asyncio.gather(ctx.book(m, seat0, s, cat=cat, sc=sc), probing())
        if bk.status == 201:
            provider = (bk.body.get("payment") or {}).get("provider")
            if provider != expect_provider:
                ctx.rec.violation(cat, sc, f"счёт {provider}, ожидали {expect_provider}", booking=bk.body["id"])
            if expect_provider == "manual":
                ctx.expected_open_at_bank[f"payment-{bk.body['payment']['id']}"] = sc

    async def create_error():
        sc = "kaspi_create_500"
        m = await ctx.new_master(cat=cat, sc=sc)
        await ctx.mock.behaviour(m.phone, create="error")
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        if bk.status == 201 and (bk.body.get("payment") or {}).get("provider") != "manual":
            ctx.rec.violation(cat, sc, "при сбое Kaspi счёт должен остаться ручным", booking=bk.body)

    async def cancel_fails_then_paid():
        sc = "kaspi_cancel_500_then_paid"
        m = await ctx.new_master(cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        pay = ctx.card_payment(cat, sc, bk) if bk.status == 201 else None
        if pay is None:
            return
        await ctx.mock.behaviour(m.phone, cancel="error")
        await ctx.cancel(m, bk.body["id"], cat=cat, sc=sc)
        order = order_of(pay["id"])
        ctx.expected_open_at_bank[order] = sc
        result = await ctx.mock.pay(order, cat, sc)
        if result.get("paid"):
            ctx.expected_open_at_bank.pop(order, None)
            bal = await ctx.balance(m, cat=cat, sc=sc)
            if bal != price:
                ctx.rec.violation(cat, sc, f"оплата по отменённой брони: баланс {bal}, ожидали {price}",
                                  booking=bk.body["id"], payment=pay["id"])

    async def lost_webhook():
        sc = "kaspi_webhook_lost"
        m = await ctx.new_master(cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        pay = ctx.card_payment(cat, sc, bk) if bk.status == 201 else None
        if pay is None:
            return
        await ctx.mock.pay(order_of(pay["id"]), cat, sc, deliver=False)
        await ctx.sync(m, pay["id"], cat=cat, sc=sc)
        state = await ctx.booking(m, bk.body["id"], cat=cat, sc=sc)
        if state["status"] != S_CONFIRMED:
            ctx.rec.violation(cat, sc, f"после sync бронь в статусе {state['status']}", booking=bk.body["id"])

    async def early_webhook():
        sc = "kaspi_webhook_before_create_response"
        m = await ctx.new_master(cat=cat, sc=sc)
        await ctx.mock.behaviour(m.phone, create="pay_on_create")
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        if bk.status != 201:
            return
        state = await ctx.booking(m, bk.body["id"], cat=cat, sc=sc)
        payment = state.get("payment") or bk.body.get("payment") or {}
        if state["status"] == S_CONFIRMED:
            return
        ctx.rec.finding(cat, sc, "Kaspi сообщил об оплате раньше, чем сервис сохранил id счёта: вебхук "
                                 "отброшен как «неизвестный счёт», бронь не подтверждена — деньги в банке, "
                                 "сервис узнает о них только по ручному sync", booking=bk.body["id"],
                        payment=payment.get("id"))
        if payment.get("id"):
            await ctx.sync(m, payment["id"], cat=cat, sc=sc)
            state = await ctx.booking(m, bk.body["id"], cat=cat, sc=sc)
            if state["status"] != S_CONFIRMED:
                ctx.rec.violation(cat, sc, f"и после sync бронь в статусе {state['status']}",
                                  booking=bk.body["id"])

    async def amount_mismatch():
        sc = "kaspi_amount_mismatch"
        m = await ctx.new_master(cat=cat, sc=sc)
        bk = await ctx.book(m, seat0, ctx.slot(), cat=cat, sc=sc)
        pay = ctx.card_payment(cat, sc, bk) if bk.status == 201 else None
        if pay is None:
            return
        order = order_of(pay["id"])
        ctx.expected_unapplied[order] = sc
        await ctx.mock.pay(order, cat, sc, amount_delta=1)
        state = await ctx.booking(m, bk.body["id"], cat=cat, sc=sc)
        if state["status"] != S_PENDING:
            ctx.rec.violation(cat, sc, f"вебхук с чужой суммой изменил бронь: {state['status']}",
                              booking=bk.body["id"])

    # 15 с — таймаут httpx в ApipayProvider: 5 с успевает, 20 с — нет
    await ctx.guard(cat, "kaspi_slow_5s", slow_create(5, "apipay", "kaspi_slow_5s"))
    await ctx.guard(cat, "kaspi_timeout_20s", slow_create(20, "manual", "kaspi_timeout_20s"))
    for _ in range(max(1, ROUNDS // 2)):
        for name, fn in (("kaspi_create_500", create_error), ("kaspi_cancel_500_then_paid", cancel_fails_then_paid),
                         ("kaspi_webhook_lost", lost_webhook), ("kaspi_webhook_before_create_response", early_webhook),
                         ("kaspi_amount_mismatch", amount_mismatch)):
            await ctx.guard(cat, name, fn())


# ---------------------------------------------------------------------------
# 6. Фоновые задачи и Telegram
# ---------------------------------------------------------------------------


async def c6_background(ctx: Ctx) -> None:
    cat = C6
    price = HOURLY_RATE

    async def reminders_parallel():
        """5 параллельных рассылок напоминаний (как если бы beat запустился несколько раз):
        каждый тестовый мастер получает каждое напоминание не больше одного раза."""
        from tg_studio.db.session import async_session_factory
        from tg_studio.modules import notify
        from tg_studio.modules.coworking.reminders import send_due_reminders

        sc = "reminders_parallel_runs"
        n = max(1, min(5, ROUNDS))
        start_paid, start_unpaid = grid_after(30 * 60), grid_after(40 * 60)
        paid_masters, unpaid_masters, ids = [], [], []
        for _ in range(n):
            m = await ctx.new_master(balance=price, cat=cat, sc=sc)
            bk = await ctx.book(m, ctx.near_seat(start_paid, start_paid + timedelta(hours=1)), start_paid,
                                cat=cat, sc=sc)
            if bk.status == 201 and (await ctx.pay_balance(m, bk.body["id"], cat=cat, sc=sc)).status == 200:
                paid_masters.append(m.tg_id)
                ids.append(bk.body["id"])
            u = await ctx.new_master(cat=cat, sc=sc)
            bk = await ctx.book(u, ctx.near_seat(start_unpaid, start_unpaid + timedelta(hours=1)), start_unpaid,
                                cat=cat, sc=sc)
            if bk.status == 201:
                unpaid_masters.append(u.tg_id)
                ids.append(bk.body["id"])

        sends: Counter = Counter()
        orig_text, orig_button = notify.send_text_to_telegram, notify.send_text_with_button

        # сообщения тестовым мастерам считаем, остальным (живой бизнес на стенде) — отправляем как есть
        async def fake_text(chat_id, text_):
            if chat_id in ctx.test_tg_ids:
                sends[("payment", chat_id)] += 1
                return 1
            return await orig_text(chat_id, text_)

        async def fake_button(chat_id, text_, button_text, callback_data):
            if chat_id in ctx.test_tg_ids:
                sends[("booking", chat_id)] += 1
                return 1
            return await orig_button(chat_id, text_, button_text, callback_data)

        async def one_run():
            async with async_session_factory() as session:
                return await send_due_reminders(session)

        notify.send_text_to_telegram, notify.send_text_with_button = fake_text, fake_button
        try:
            await burst([one_run for _ in range(5)])
        finally:
            notify.send_text_to_telegram, notify.send_text_with_button = orig_text, orig_button
        for (kind, chat_id), count in sends.items():
            if count > 1 and (chat_id in paid_masters or chat_id in unpaid_masters):
                ctx.rec.violation(cat, sc, f"напоминание «{kind}» отправлено {count} раз", telegram_id=chat_id)
        async with async_session_factory() as session:
            rows = (await session.execute(
                text("select id, status, payment_reminder_sent_at, booking_reminder_sent_at "
                     "from bookings where id = any(:ids)"), {"ids": ids})).all()
        for bid, status, pay_sent, book_sent in rows:
            if status == S_CONFIRMED and book_sent is None:
                ctx.rec.violation(cat, sc, "напоминание о брони не отмечено отправленным", booking=bid)
            if status == S_PENDING and pay_sent is None:
                ctx.rec.violation(cat, sc, "напоминание об оплате не отмечено отправленным", booking=bid)

    async def presence_presses():
        sc = "presence_button_parallel"
        m = await ctx.new_master(balance=price, cat=cat, sc=sc)
        start = grid_after(30 * 60)
        bk = await ctx.book(m, ctx.near_seat(start, start + timedelta(hours=1)), start, cat=cat, sc=sc)
        if bk.status != 201 or (await ctx.pay_balance(m, bk.body["id"], cat=cat, sc=sc)).status != 200:
            return
        bid = bk.body["id"]

        def press(from_id: int):
            update = {"update_id": 1, "callback_query": {
                "id": f"lt-{bid}-{from_id}", "from": {"id": from_id}, "data": f"presence:{bid}",
                "message": {"message_id": 1, "chat": {"id": from_id}, "text": "loadtest"}}}
            return ctx.api.call(None, "POST", "/api/telegram/webhook", cat=cat, sc=sc, json_body=update,
                                headers={"X-Telegram-Bot-Api-Secret-Token": settings.telegram_webhook_secret})

        stranger = ctx.run_id * 1000 + 999
        res = await burst([lambda: press(m.tg_id) for _ in range(BURST)] + [lambda: press(stranger)] * 2)
        own, foreign = res[:BURST], res[BURST:]
        if not any(isinstance(r.body, dict) and r.body.get("confirmed") for r in own):
            ctx.rec.violation(cat, sc, "ни одно нажатие мастера не подтвердило присутствие", booking=bid)
        if any(isinstance(r.body, dict) and r.body.get("confirmed") for r in foreign):
            ctx.rec.violation(cat, sc, "чужой Telegram подтвердил присутствие", booking=bid)
        events = await ctx.history(m, bid, cat=cat, sc=sc)
        count = sum(e["kind"] == "присутствие_подтверждено" for e in events)
        if count != 1:
            ctx.rec.violation(cat, sc, f"событий присутствия {count}, ожидалось 1", booking=bid)

    await asyncio.gather(ctx.guard(cat, "reminders_parallel_runs", reminders_parallel()),
                         ctx.guard(cat, "presence_button_parallel", presence_presses()))


# ---------------------------------------------------------------------------
# 7. Общая нагрузка
# ---------------------------------------------------------------------------


async def c7_load(ctx: Ctx) -> None:
    cat = C7
    vus = max(LOAD_STAGES) if LOAD_STAGES else 0
    if not vus:
        return
    masters = [await ctx.new_master(balance=10_000_000, cat=cat, sc="setup") for _ in range(vus)]
    hot = [ctx.slot() for _ in range(3)]  # общие слоты — конкуренция и ожидаемые 409

    async def vu(i: int, stop: float):
        m, rnd = masters[i], random.Random(SEED * 100_003 + i)
        while time.monotonic() < stop:
            action = rnd.choices(
                ["calendar", "availability", "my_bookings", "balance", "dashboard", "report",
                 "book_balance", "book_card", "book_cancel"],
                weights=[15, 15, 10, 10, 5, 5, 20, 10, 10],
            )[0]
            now = datetime.now(STUDIO_TZ)
            if action == "calendar":
                await ctx.api.call(m, "GET", "/api/calendar", cat=cat, sc=action,
                                   params={"from": now.isoformat(), "to": (now + timedelta(days=7)).isoformat()})
            elif action == "availability":
                s = rnd.choice(hot)
                await ctx.api.call(m, "GET", "/api/bookings/availability", cat=cat, sc=action,
                                   params={"start": s.isoformat(), "end": (s + timedelta(hours=1)).isoformat()})
            elif action == "my_bookings":
                await ctx.api.call(m, "GET", "/api/bookings", cat=cat, sc=action, params={"limit": 50})
            elif action == "balance":
                await ctx.api.call(m, "GET", "/api/me/balance", cat=cat, sc=action)
            elif action == "dashboard":
                await ctx.api.call(ctx.owner, "GET", "/api/dashboard", cat=cat, sc=action)
            elif action == "report":
                await ctx.api.call(ctx.owner, "GET", "/api/reports/masters", cat=cat, sc=action,
                                   params={"from": (now - timedelta(days=1)).isoformat(),
                                           "to": (now + timedelta(days=30)).isoformat()})
            else:
                start = rnd.choice(hot) if rnd.random() < 0.3 else ctx.slot()
                bk = await ctx.book(m, rnd.choice(ctx.seats), start, cat=cat, sc=action, expect=(201, 409))
                if bk.status != 201:
                    continue
                bid = bk.body["id"]
                if action == "book_card":
                    pay = (bk.body.get("payment") or {})
                    if pay.get("provider") == "apipay":
                        await ctx.mock.pay(order_of(pay["id"]), cat, action)
                    continue
                await ctx.pay_balance(m, bid, cat=cat, sc=action, expect=(200, 409))
                if action == "book_cancel":
                    await ctx.cancel(m, bid, cat=cat, sc=action, expect=(200, 409))

    for n in LOAD_STAGES:
        ctx.rec.phase = f"load:{n}"
        t0 = time.time()
        stop = time.monotonic() + LOAD_STAGE_SECONDS
        await asyncio.gather(*(ctx.guard(cat, f"vu{i}", vu(i, stop)) for i in range(n)))
        ctx.rec.stage_windows[ctx.rec.phase] = (t0, time.time())


# ---------------------------------------------------------------------------
# Итоговая сверка
# ---------------------------------------------------------------------------

AUDIT_SQL = {
    "seat_overlap": """
        with h as (select id, seat_id, start, "end" from bookings where business_id = :b and
                   (status in ('подтверждена', 'начата') or (status = 'ожидает_оплаты' and hold_deadline > now())))
        select a.id, b.id, a.seat_id from h a join h b
          on a.seat_id = b.seat_id and a.id < b.id and a.start < b."end" and b.start < a."end" """,
    "master_overlap": """
        with h as (select id, master_id, start, "end" from bookings where business_id = :b and
                   (status in ('подтверждена', 'начата') or (status = 'ожидает_оплаты' and hold_deadline > now())))
        select a.id, b.id, a.master_id from h a join h b
          on a.master_id = b.master_id and a.id < b.id and a.start < b."end" and b.start < a."end" """,
    "negative_balance": """
        select master_id, sum(amount) from ledger_entries where business_id = :b
        group by master_id having sum(amount) < 0""",
    "topup_vs_paid_payment": """
        select p.id, p.status, p.amount, count(l.id), coalesce(sum(l.amount), 0)
        from payments p left join ledger_entries l on l.payment_id = p.id and l.kind = 'topup'
        where p.business_id = :b and p.master_id is not null
        group by p.id
        having (p.status = 'paid' and (count(l.id) <> 1 or coalesce(sum(l.amount), 0) <> p.amount))
            or (p.status <> 'paid' and count(l.id) <> 0)""",
    "booking_money": """
        with net as (select booking_id, sum(amount) as amount from ledger_entries
                     where business_id = :b and kind in ('charge', 'refund') and booking_id is not null
                     group by booking_id),
             ref as (select booking_id, sum((details->>'amount')::numeric) as amount from booking_events
                     where business_id = :b and kind = 'возвращена' group by booking_id)
        select bk.id, bk.status, bk.paid_amount, (bk.price_snapshot->>'final')::numeric,
               coalesce(net.amount, 0), coalesce(ref.amount, 0)
        from bookings bk left join net on net.booking_id = bk.id left join ref on ref.booking_id = bk.id
        where bk.business_id = :b and not (
          (bk.status in ('подтверждена', 'начата')
             and bk.paid_amount = (bk.price_snapshot->>'final')::numeric
             and -coalesce(net.amount, 0) = bk.paid_amount)
          or (bk.status in ('ожидает_оплаты', 'истекла_оплата')
             and bk.paid_amount = 0 and coalesce(net.amount, 0) = 0)
          or (bk.status in ('отменена_мастером', 'отменена_площадкой')
             and coalesce(ref.amount, 0) <= bk.paid_amount
             and -coalesce(net.amount, 0) = bk.paid_amount - coalesce(ref.amount, 0)
             and (bk.status <> 'отменена_площадкой' or coalesce(ref.amount, 0) = bk.paid_amount)))""",
    "pending_invoices_per_booking": """
        select bk.id, bk.status, count(p.id) from bookings bk
        join payments p on p.booking_id = bk.id and p.status = 'pending'
        where bk.business_id = :b
        group by bk.id having count(p.id) > 1 or bk.status <> 'ожидает_оплаты'""",
    "duplicate_events": """
        select booking_id, kind, count(*) from booking_events
        where business_id = :b and kind in ('оплачена', 'отменена', 'истекла_оплата', 'присутствие_подтверждено')
        group by booking_id, kind having count(*) > 1""",
    "events_after_terminal": """
        select later.booking_id, term.kind, later.kind from booking_events term
        join booking_events later on later.booking_id = term.booking_id and later.id > term.id
        where term.business_id = :b and (
          (term.kind in ('отменена', 'истекла_оплата') and later.kind in ('оплачена', 'перенесена', 'продлена'))
          or (term.kind = 'оплачена' and later.kind = 'перенесена'))""",
    "stale_holds": f"""
        select id, hold_deadline from bookings where business_id = :b and status = 'ожидает_оплаты'
        and hold_deadline < now() - interval '{SWEEP_STALE_MINUTES} minutes'""",
}

AUDIT_TITLES = {
    "seat_overlap": "Пересечения активных броней на одном месте",
    "master_overlap": "Пересечения активных броней одного мастера",
    "negative_balance": "Отрицательный баланс мастера",
    "topup_vs_paid_payment": "Оплаченный счёт ↔ ровно одно зачисление на его сумму",
    "booking_money": "Деньги брони: списано = оплачено − возвращено",
    "pending_invoices_per_booking": "Живые счета: не больше одного и только у ожидающей оплаты брони",
    "duplicate_events": "Повторные события (оплата/отмена/истечение/присутствие)",
    "events_after_terminal": "События после отмены/истечения, перенос после оплаты",
    "stale_holds": f"Hold просрочен больше {SWEEP_STALE_MINUTES} мин, бронь не истекла (свип не работает)",
}


async def audit(ctx: Ctx) -> None:
    from tg_studio.db.session import async_session_factory

    rec, cat = ctx.rec, "audit"
    rec.phase = "audit"
    async with async_session_factory() as session:
        for name, sql in AUDIT_SQL.items():
            rows = [list(r) for r in (await session.execute(text(sql), {"b": ctx.business_id})).all()]
            rec.audit.append({"check": name, "title": AUDIT_TITLES[name], "rows": len(rows)})
            for row in rows[:200]:
                rec.violation(cat, name, AUDIT_TITLES[name], row=[str(v) for v in row])
        payments = {
            row[0]: {"status": row[1], "provider": row[2], "external": row[3], "booking": row[4], "amount": row[5]}
            for row in (await session.execute(
                text("select id, status, provider, external_invoice_id, booking_id, amount from payments "
                     "where business_id = :b"), {"b": ctx.business_id})).all()
        }

    # Сверка с банком: мок знает, что на самом деле оплачено
    invoices = await ctx.mock.invoices()
    (rec.dir / "mock_invoices.json").write_text(json.dumps(invoices, ensure_ascii=False, indent=1))
    ours = {f"payment-{pid}": p for pid, p in payments.items()}
    bank = [inv for inv in invoices if inv["external_order_id"] in ours]
    by_order: dict[str, list[dict]] = defaultdict(list)
    for inv in bank:
        by_order[inv["external_order_id"]].append(inv)

    unapplied = []
    for order, invs in by_order.items():
        p = ours[order]
        if len(invs) > 1:
            rec.finding(cat, "bank_duplicate_invoices", "на один платёж в банке несколько счетов", order=order,
                        invoices=[i["id"] for i in invs])
        for inv in invs:
            if inv["status"] == "paid" and p["status"] != "paid":
                if order in ctx.expected_unapplied:
                    continue
                unapplied.append((order, inv))
            elif inv["status"] != "paid" and p["status"] == "paid" and p["provider"] == "apipay" \
                    and p["external"] == inv["id"]:
                rec.violation(cat, "bank_reconciliation", "в сервисе оплачен, в банке нет", order=order,
                              invoice=inv, payment=p)
            elif inv["status"] == "pending" and p["status"] in ("cancelled", "expired"):
                if order in ctx.expected_open_at_bank:
                    continue
                rec.finding(cat, "bank_open_invoice",
                            "счёт закрыт в сервисе, но открыт в банке — мастер ещё может по нему заплатить "
                            "(деньги лягут на баланс)", order=order, invoice=inv["id"], payment=p)
            elif inv["status"] == "pending" and p["external"] != inv["id"] and p["status"] == "pending":
                rec.finding(cat, "bank_orphan_invoice", "счёт создан в банке (клиент видит его в Kaspi по номеру), "
                                                        "а сервис о нём не знает — оплата по нему не применится",
                            order=order, invoice=inv["id"], payment=p)

    # «оплачено в банке, не в сервисе»: проверяем, лечит ли ручной опрос
    healed = 0
    for order, inv in unapplied:
        pid = int(order.removeprefix("payment-"))
        p = ours[order]
        if p["provider"] == "apipay" and p["external"] == inv["id"]:
            r = await ctx.sync(ctx.owner, pid, cat=cat, sc="bank_reconciliation_sync")
            if isinstance(r.body, dict) and r.body.get("status") == "paid":
                healed += 1
                rec.finding(cat, "bank_reconciliation", "деньги были в банке, сервис узнал о них только "
                                                        "после ручного sync", order=order, invoice=inv["id"])
                continue
        rec.violation(cat, "bank_reconciliation", "оплачено в банке, в сервисе нет (деньги потеряны)",
                      order=order, invoice=inv, payment=p)
    rec.audit.append({"check": "bank_paid_not_applied", "title": "Оплачено в банке, не учтено в сервисе",
                      "rows": len(unapplied), "healed_by_sync": healed})


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------


_LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


def collect_service_logs(rec: Recorder) -> dict[str, int]:
    """Вырезать из логов сервисов (loadtest/pyhook/sitecustomize.py) записи за время
    прогона в папку отчёта. Строки без метки времени — продолжение записи (трейсбек)."""
    src = Path(os.environ.get("LOADTEST_LOG_DIR", str(REPORT_ROOT / "service-logs")))
    since = rec.started.replace(tzinfo=None, microsecond=0)
    counts: dict[str, int] = {}
    for path in sorted(src.glob("*.log")):
        keep, records, out = False, 0, []
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _LOG_TS.match(line)
                if m:
                    keep = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") >= since
                    records += keep
                if keep:
                    out.append(line)
        if out:
            name = f"service-{path.name}"
            (rec.dir / name).write_text("".join(out), encoding="utf-8")
            counts[name] = records
    return counts


def write_report(ctx: Ctx, finished: datetime) -> Path:
    rec = ctx.rec
    service_logs = collect_service_logs(rec)
    (rec.dir / "violations.json").write_text(json.dumps(rec.violations, ensure_ascii=False, indent=1, default=str))
    (rec.dir / "findings.json").write_text(json.dumps(rec.findings, ensure_ascii=False, indent=1, default=str))
    (rec.dir / "script_errors.json").write_text(json.dumps(rec.script_errors, ensure_ascii=False, indent=1))

    lines = [f"# Нагрузочный тест {rec.started:%Y-%m-%d %H:%M:%S} UTC", ""]
    lines += [
        f"- длительность: {(finished - rec.started).total_seconds():.0f} с; бизнес #{ctx.business_id}, "
        f"владелец {ctx.owner.email if ctx.owner else '—'}",
        f"- параметры: ROUNDS={ROUNDS} BURST={BURST} SEATS={SEATS} LOAD_STAGES={LOAD_STAGES} "
        f"LOAD_STAGE_SECONDS={LOAD_STAGE_SECONDS} SEED={SEED}",
        f"- нарушений: **{len(rec.violations)}**, находок: {len(rec.findings)}, "
        f"ошибок самого скрипта: {len(rec.script_errors)}",
        "",
        "Логи сервиса за прогон (WARNING+, трейсбеки 500, сбои задач): "
        + (", ".join(f"`{name}` ({n} записей)" for name, n in service_logs.items()) or "пусто"),
        "",
        "## Категории",
        "",
        "| Категория | Запросов | 5xx/нет ответа | Нарушений | Находок |",
        "|---|---|---|---|---|",
    ]
    for c, title in CATEGORY_TITLES.items():
        reqs = [s for (ph, cc, _, _), v in rec.stats.items() if cc == c for s in v]
        bad = sum(1 for st, _ in reqs if st is None or st >= 500)
        lines.append(f"| {title} | {len(reqs)} | {bad} | {sum(v['category'] == c for v in rec.violations)} | "
                     f"{sum(f['category'] == c for f in rec.findings)} |")
    lines.append(f"| Итоговая сверка | — | — | {sum(v['category'] == 'audit' for v in rec.violations)} | "
                 f"{sum(f['category'] == 'audit' for f in rec.findings)} |")

    lines += ["", "## Итоговая сверка", "", "| Проверка | Строк-нарушений |", "|---|---|"]
    for a in rec.audit:
        extra = f" (вылечено sync: {a['healed_by_sync']})" if "healed_by_sync" in a else ""
        lines.append(f"| {a['title']} | {a['rows']}{extra} |")

    def scenario_table(phase_filter):
        rows = defaultdict(list)
        for (ph, c, sc, ep), v in rec.stats.items():
            if phase_filter(ph):
                rows[(c, sc, ep)].extend(v)
        out = ["| Категория | Сценарий | Запрос | N | p50 мс | p95 мс | p99 мс | статусы |", "|---|---|---|---|---|---|---|---|"]
        for (c, sc, ep), v in sorted(rows.items()):
            ms = [x for _, x in v]
            statuses = Counter(str(s) for s, _ in v)
            out.append(f"| {c} | {sc} | {ep} | {len(v)} | {_pct(ms, 50):.0f} | {_pct(ms, 95):.0f} | "
                       f"{_pct(ms, 99):.0f} | {dict(statuses)} |")
        return out

    lines += ["", "## Нагрузка по ступеням", "",
              "| Пользователей | Запросов | RPS | p50 мс | p95 мс | p99 мс | 5xx | 409 |", "|---|---|---|---|---|---|---|---|"]
    for ph, (t0, t1) in rec.stage_windows.items():
        v = [s for (p, _, _, _), vals in rec.stats.items() if p == ph for s in vals]
        ms = [x for _, x in v]
        lines.append(f"| {ph.removeprefix('load:')} | {len(v)} | {len(v) / max(t1 - t0, 1e-9):.1f} | "
                     f"{_pct(ms, 50):.0f} | {_pct(ms, 95):.0f} | {_pct(ms, 99):.0f} | "
                     f"{sum(1 for s, _ in v if s is None or s >= 500)} | {sum(1 for s, _ in v if s == 409)} |")
    lines += ["", "### По запросам на каждой ступени", ""] + scenario_table(lambda ph: ph.startswith("load:"))
    lines += ["", "## Атаки: время ответа по сценариям", "",
              "Сравните `*_probe` у сбоев шлюза с обычным чтением: рост значит, что медленный Kaspi "
              "тормозит посторонние запросы.", ""] + scenario_table(lambda ph: ph == "attack")

    def listing(title, items, limit=100):
        out = ["", f"## {title} ({len(items)})", ""]
        for it in items[:limit]:
            details = {k: v for k, v in it.items() if k not in ("at", "category", "scenario", "message")}
            out.append(f"- **{it['category']} / {it['scenario']}** — {it['message']}  ")
            out.append(f"  `{json.dumps(details, ensure_ascii=False, default=str)[:600]}`")
        if len(items) > limit:
            out.append(f"- … ещё {len(items) - limit}, полный список в json")
        return out

    lines += listing("Нарушения", rec.violations)
    lines += listing("Находки", rec.findings)
    if rec.script_errors:
        lines += ["", f"## Ошибки скрипта ({len(rec.script_errors)})", ""]
        lines += [f"- {e['category']} / {e['scenario']}: `{e['error']}`" for e in rec.script_errors[:50]]
    path = rec.dir / "summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def main() -> None:
    rec = Recorder()
    ctx = Ctx(rec)
    print(f"Отчёт: {rec.dir}", flush=True)
    try:
        await setup(ctx)
        print(f"Бизнес #{ctx.business_id}, мест {len(ctx.seats)}. Атаки…", flush=True)
        rec.phase = "attack"
        await asyncio.gather(
            ctx.guard(C1, "category", c1_double_booking(ctx)),
            ctx.guard(C2, "category", c2_double_money(ctx)),
            ctx.guard(C3, "category", c3_pay_vs_cancel(ctx)),
            ctx.guard(C4, "category", c4_booking_changes(ctx)),
            ctx.guard(C5, "category", c5_gateway_faults(ctx)),
            ctx.guard(C6, "category", c6_background(ctx)),
        )
        print("Нагрузка по ступеням…", flush=True)
        await ctx.guard(C7, "category", c7_load(ctx))
        print("Сверка…", flush=True)
        await ctx.guard("audit", "audit", audit(ctx))
    except Exception as exc:
        rec.script_error("main", rec.phase, exc)
    finally:
        rec.close()
        path = write_report(ctx, datetime.now(UTC))
        print(f"Готово: {path}\nНарушений: {len(rec.violations)}, находок: {len(rec.findings)}, "
              f"ошибок скрипта: {len(rec.script_errors)}", flush=True)
        await ctx.api.http.aclose()
        await ctx.mock.http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
