"""
API тату-работ и сеансов (миниапп: владелец и мастера).

Доступ — штатная авторизация миниаппа (TelegramInitData / debug-заголовок):
- мастер видит и правит только свои работы;
- владелец — все работы бизнеса, мастер выбирается параметром master_id.
Сеансы синхронизируются с Google Calendar.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tg_studio.api.admin_deps import (
    MasterBusinessAndSelfDep,
    MasterBusinessDep,
    get_master_business,
)
from tg_studio.api.auth import CurrentUserOptionalDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import (
    Business,
    Client,
    ClientSource,
    Master,
    TattooFile,
    TattooFileKind,
    TattooProjectStatus,
    TattooSession,
    TattooWork,
    TattooWorkStatus,
)
from tg_studio.modules.chat.service import make_file_token, verify_file_token
from tg_studio.modules.google_calendar.client import (
    cancel_event,
    create_event,
    list_events,
    update_event,
)
from tg_studio.modules.tattoo import files as files_storage
from tg_studio.modules.tattoo.pricing import effective_pricing, estimate_price
from tg_studio.modules.tattoo.schemas import (
    COMPLEXITY_OPTIONS,
    DIFFICULT_PLACEMENTS,
    PriceEstimateResponse,
    TattooClientCreate,
    TattooClientOut,
    TattooClientUpdate,
    TattooFileOut,
    TattooOptionOut,
    TattooOptionsResponse,
    TattooPlacement,
    TattooSessionCreate,
    TattooSessionResponse,
    TattooSessionUpdate,
    TattooStyle,
    TattooWorkCreate,
    TattooWorkListResponse,
    TattooWorkResponse,
    TattooWorkUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tattoo", tags=["tattoo"])


# ---------------------------------------------------------------------------
# Авторизация и резолв мастера
# ---------------------------------------------------------------------------


async def _resolve_master(
    session: SessionDep, business: Business, self_master: Master | None, master_id: int | None
) -> Master:
    """Мастер, к которому относится операция.

    Мастер всегда действует от себя; владелец указывает master_id явно.
    """
    if self_master is not None:
        return self_master
    if master_id is None:
        raise HTTPException(status_code=422, detail="Укажите master_id")
    result = await session.execute(
        select(Master).where(
            Master.id == master_id,
            Master.business_id == business.id,
            Master.is_active.is_(True),
        )
    )
    master = result.scalar_one_or_none()
    if not master:
        raise HTTPException(status_code=404, detail="Мастер не найден в этом бизнесе")
    return master


# ---------------------------------------------------------------------------
# Защита от дубля записи (два слоя: сеансы в БД + события Google Calendar)
# ---------------------------------------------------------------------------

STUDIO_TZ = ZoneInfo("Asia/Almaty")
# Длительность сеанса в БД не хранится; в календарь уходит 3 часа, её же
# используем для проверки пересечений (у мастера может быть своя дефолтная)
SESSION_FALLBACK_MINUTES = 180


def _master_duration(master: Master) -> timedelta:
    return timedelta(minutes=master.default_duration_minutes or SESSION_FALLBACK_MINUTES)


def _ensure_tz(dt: datetime) -> datetime:
    """Наивное время считаем временем студии (фронт шлёт ISO без зоны)."""
    return dt.replace(tzinfo=STUDIO_TZ) if dt.tzinfo is None else dt


def _fmt_dt(dt: datetime) -> str:
    local = dt.replace(tzinfo=STUDIO_TZ) if dt.tzinfo is None else dt.astimezone(STUDIO_TZ)
    return local.strftime("%d.%m %H:%M")


def _event_interval(event: dict) -> tuple[datetime, datetime] | None:
    """[start, end) события календаря; all-day считаем занятым до полуночи."""
    start_json, end_json = event.get("start") or {}, event.get("end") or {}
    if "dateTime" in start_json and "dateTime" in end_json:
        return (
            datetime.fromisoformat(start_json["dateTime"].replace("Z", "+00:00")),
            datetime.fromisoformat(end_json["dateTime"].replace("Z", "+00:00")),
        )
    if "date" in start_json and "date" in end_json:
        return (
            datetime.fromisoformat(start_json["date"]).replace(tzinfo=STUDIO_TZ),
            datetime.fromisoformat(end_json["date"]).replace(tzinfo=STUDIO_TZ),
        )
    return None


async def _assert_slot_free(
    db: SessionDep,
    business: Business,
    master: Master,
    start: datetime,
    end: datetime,
    *,
    exclude_session_id: int | None = None,
    exclude_event_id: str | None = None,
) -> None:
    """409, если у мастера в интервале [start, end) уже что-то есть.

    Два слоя: сеансы в БД (живут всегда) и события Google Calendar,
    созданные руками в календаре мимо CRM.
    """
    duration = _master_duration(master)

    stmt = (
        select(TattooSession, TattooWork, Client)
        .join(TattooWork, TattooWork.id == TattooSession.work_id)
        .join(Client, Client.id == TattooWork.client_id)
        .where(
            TattooWork.business_id == business.id,
            TattooWork.master_id == master.id,
            TattooWork.status != TattooWorkStatus.cancelled,
            TattooSession.status != TattooProjectStatus.cancelled,
            # Пересечение интервалов [es, es+dur) × [start, end):
            # es < end и es + dur > start; dur общий, поэтому es > start - dur
            TattooSession.session_date < end,
            TattooSession.session_date > start - duration,
        )
        .order_by(TattooSession.session_date)
    )
    if exclude_session_id is not None:
        stmt = stmt.where(TattooSession.id != exclude_session_id)
    rows = (await db.execute(stmt)).all()

    conflicts = [
        f"сеанс работы #{work.id} ({client.full_name}) "
        f"{_fmt_dt(s.session_date)}–{_fmt_dt(s.session_date + duration)}"
        for s, work, client in rows
    ]

    if business.google_calendar_credentials_json:
        # событие, зеркалящее найденный сеанс, — тот же конфликт, не два
        mirrored = {s.google_event_id for s, _, _ in rows if s.google_event_id}
        # окно шире интервала: событие может начаться раньше и закончиться внутри
        events = await list_events(
            credentials_json=business.google_calendar_credentials_json,
            time_min=(start - timedelta(hours=12)).isoformat(),
            time_max=(end + timedelta(hours=12)).isoformat(),
            calendar_id=master.google_calendar_id or "primary",
        )
        for event in events:
            interval = _event_interval(event)
            if interval is None:
                continue
            ev_start, ev_end = interval
            if (
                ev_start < end
                and ev_end > start
                and event.get("id") != exclude_event_id
                and event.get("id") not in mirrored
            ):
                conflicts.append(
                    f"событие в Google Calendar «{event.get('summary') or 'без названия'}» "
                    f"{_fmt_dt(ev_start)}–{_fmt_dt(ev_end)}"
                )

    if conflicts:
        raise HTTPException(
            status_code=409,
            detail="В это время у мастера уже занято: " + "; ".join(conflicts),
        )


# ---------------------------------------------------------------------------
# Google Calendar helpers
# ---------------------------------------------------------------------------


async def _sync_session_to_calendar(
    tattoo_session: TattooSession,
    work: TattooWork,
    master: Master,
    business: Business,
) -> str | None:
    """Создать или обновить событие в Google Calendar для сеанса."""
    if not business.google_calendar_credentials_json:
        return None

    summary = f"Тату: {work.size_length_cm}×{work.size_height_cm} см, {work.complexity} — {master.full_name}"
    if tattoo_session.cost is not None:
        cost_line = f"Стоимость сеанса: {tattoo_session.cost} KZT"
    elif tattoo_session.recommended_price is not None:
        cost_line = f"Рекомендуемая цена: {tattoo_session.recommended_price} KZT"
    else:
        cost_line = "Стоимость сеанса: не указана"
    description = (
        f"Работа #{work.id}, сеанс #{tattoo_session.id}\n"
        f"Мастер: {master.full_name}\n"
        f"Студия: {business.name}\n"
        f"Размер: {work.size_length_cm}×{work.size_height_cm} см\n"
        f"Сложность: {work.complexity}\n"
        f"Стиль: {work.style}\n"
        f"Место: {work.placement}\n"
        + cost_line
    )

    start_dt = tattoo_session.session_date.isoformat()
    # Длительность по умолчанию — 3 часа
    end_dt = (tattoo_session.session_date + timedelta(hours=3)).isoformat()
    calendar_id = master.google_calendar_id or "primary"

    if tattoo_session.google_event_id:
        success = await update_event(
            credentials_json=business.google_calendar_credentials_json,
            event_id=tattoo_session.google_event_id,
            summary=summary,
            description=description,
            start_datetime=start_dt,
            end_datetime=end_dt,
            calendar_id=calendar_id,
        )
        return tattoo_session.google_event_id if success else None
    else:
        event_id = await create_event(
            credentials_json=business.google_calendar_credentials_json,
            summary=summary,
            description=description,
            start_datetime=start_dt,
            end_datetime=end_dt,
            calendar_id=calendar_id,
        )
        return event_id


async def _remove_session_from_calendar(
    tattoo_session: TattooSession,
    master: Master,
    business: Business,
) -> None:
    """Удалить событие из Google Calendar."""
    if tattoo_session.google_event_id and business.google_calendar_credentials_json:
        await cancel_event(
            credentials_json=business.google_calendar_credentials_json,
            event_id=tattoo_session.google_event_id,
            calendar_id=master.google_calendar_id or "primary",
        )


# ---------------------------------------------------------------------------
# TattooWork CRUD
# ---------------------------------------------------------------------------


def _work_load_options():
    """Сеансы вместе с их фото — иначе async lazy-load уронит сериализацию."""
    return [selectinload(TattooWork.sessions).selectinload(TattooSession.files)]


def _file_url(record: TattooFile) -> str:
    """Подписанная ссылка (TTL час): работает и в <img>, и как прямое открытие."""
    return f"/api/tattoo/files/{record.id}?t={make_file_token(record.id)}"


def _attach_file_urls(sessions) -> None:
    """file_url проставляется атрибутом ORM-объекта — from_attributes заберёт."""
    for s in sessions:
        for f in s.files:
            f.file_url = _file_url(f)


async def _get_work_or_404(
    session: SessionDep, work_id: int, business: Business, self_master: Master | None
) -> TattooWork:
    """Работа бизнеса; мастер видит только собственные."""
    filters = [TattooWork.id == work_id, TattooWork.business_id == business.id]
    if self_master is not None:
        filters.append(TattooWork.master_id == self_master.id)
    result = await session.execute(
        select(TattooWork).where(*filters).options(*_work_load_options())
    )
    work = result.scalar_one_or_none()
    if not work:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    return work


async def _reload_work(session: SessionDep, work_id: int) -> TattooWork:
    """Перечитать работу после commit — со свежими сеансами и их фото."""
    result = await session.execute(
        select(TattooWork).where(TattooWork.id == work_id).options(*_work_load_options())
    )
    return result.scalar_one()


@router.get("/clients", response_model=list[TattooClientOut])
async def list_clients(session: SessionDep, _business: MasterBusinessDep) -> list[TattooClientOut]:
    """Список клиентов для выбора при создании работы (клиенты не привязаны к бизнесу)."""
    result = await session.execute(select(Client).order_by(Client.full_name))
    return [TattooClientOut.model_validate(c) for c in result.scalars().all()]


async def _find_duplicate(session: SessionDep, body: TattooClientCreate | TattooClientUpdate) -> Client | None:
    """Дедуп: ищем клиента по identifier'ам каналов (телефон → инста)."""
    for column, value in (
        (Client.phone, body.phone),
        (Client.instagram_username, body.instagram_username),
    ):
        if not value:
            continue
        result = await session.execute(select(Client).where(column == value))
        found = result.scalar_one_or_none()
        if found is not None:
            return found
    return None


async def _assert_identifier_free(session: SessionDep, body: TattooClientUpdate, client_id: int) -> None:
    """Телефон/инста не должны принадлежать другому клиенту."""
    other = await _find_duplicate(session, body)
    if other is not None and other.id != client_id:
        raise HTTPException(
            status_code=409,
            detail=f"Эти контакты уже у клиента «{other.full_name}» (#{other.id})",
        )


@router.post("/clients", response_model=TattooClientOut, status_code=201)
async def create_client(
    body: TattooClientCreate, session: SessionDep, response: Response, _business: MasterBusinessDep
):
    """Ручное создание клиента (пришёл не из Telegram).

    Дедуп: если телефон/инста уже в базе — возвращаем существующего клиента
    (200), дозаполнив пустые поля. Новый клиент — 201.
    """
    duplicate = await _find_duplicate(session, body)
    if duplicate is not None:
        response.status_code = 200
        # Обогащаем запись: новые каналы дописываются в существующего клиента
        changed = False
        if not duplicate.phone and body.phone:
            duplicate.phone = body.phone
            changed = True
        if not duplicate.instagram_username and body.instagram_username:
            duplicate.instagram_username = body.instagram_username
            changed = True
        if not duplicate.note and body.note:
            duplicate.note = body.note
            changed = True
        if changed:
            await session.commit()
            await session.refresh(duplicate)
        return duplicate

    client = Client(
        full_name=body.full_name.strip(),
        phone=body.phone,
        instagram_username=body.instagram_username,
        note=body.note,
        source=ClientSource.manual,
    )
    session.add(client)
    await session.commit()
    await session.refresh(client)
    return client


@router.patch("/clients/{client_id}", response_model=TattooClientOut)
async def update_client(
    client_id: int, body: TattooClientUpdate, session: SessionDep, _business: MasterBusinessDep
):
    """Правка карточки клиента (опечатки, дозаполнение контактов)."""
    client = await session.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    await _assert_identifier_free(session, body, client_id)
    for field in ("full_name", "phone", "instagram_username", "note"):
        if getattr(body, field) is not None:
            setattr(client, field, getattr(body, field))
    await session.commit()
    await session.refresh(client)
    return client


@router.get("/options", response_model=TattooOptionsResponse)
async def tattoo_options(_business: MasterBusinessDep) -> TattooOptionsResponse:
    """Справочники формы: стили, зоны (с пометкой сложности), сложность."""
    return TattooOptionsResponse(
        styles=[s.value for s in TattooStyle],
        placements=[
            TattooOptionOut(value=p.value, difficult=p in DIFFICULT_PLACEMENTS)
            for p in TattooPlacement
        ],
        complexities=COMPLEXITY_OPTIONS,
    )


@router.get("/price/estimate", response_model=PriceEstimateResponse)
async def price_estimate(
    _business: MasterBusinessDep,
    length_cm: float = Query(..., gt=0, description="Длина тату, см"),
    height_cm: float = Query(..., gt=0, description="Высота тату, см"),
    complexity: str | None = Query(None, description="Сложность (в формуле не участвует)"),
    style: TattooStyle | None = Query(None),
    placement: TattooPlacement | None = Query(None),
    coverup: bool = Query(False, description="Перекрытие / работа по шраму"),
) -> PriceEstimateResponse:
    """Рекомендуемая цена по прайсу — для автозаполнения в форме и счёта на предоплату."""
    return PriceEstimateResponse(
        recommended_price=estimate_price(
            effective_pricing(_business),
            length_cm,
            height_cm,
            style=style,
            placement=placement,
            coverup=coverup,
        )
    )


@router.get("/works", response_model=TattooWorkListResponse)
async def list_works(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    master_id: int | None = Query(default=None, description="Фильтр по мастеру (для владельца)"),
):
    """Тату-работы: мастер — свои, владелец — все по бизнесу (опционально по мастеру)."""
    business, self_master = actor
    filters = [TattooWork.business_id == business.id]
    if self_master is not None:
        filters.append(TattooWork.master_id == self_master.id)
    elif master_id is not None:
        filters.append(TattooWork.master_id == master_id)
    result = await session.execute(
        select(TattooWork)
        .where(*filters)
        .options(*_work_load_options())
        .order_by(TattooWork.created_at.desc())
    )
    works = result.scalars().all()
    for w in works:
        _attach_file_urls(w.sessions)
    return TattooWorkListResponse(
        works=[TattooWorkResponse.model_validate(w) for w in works],
        total=len(works),
    )


@router.post("/works", response_model=TattooWorkResponse, status_code=201)
async def create_work(
    body: TattooWorkCreate,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    force: bool = Query(False, description="Создать даже если время у мастера занято"),
):
    """Создать новую тату-работу вместе с первым сеансом и событием в Google Calendar."""
    business, self_master = actor
    master = await _resolve_master(session, business, self_master, body.master_id)
    client = await session.get(Client, body.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    first_start = _ensure_tz(body.first_session.session_date)
    if not force:
        await _assert_slot_free(
            session,
            business,
            master,
            first_start,
            first_start + _master_duration(master),
        )

    work = TattooWork(
        client_id=body.client_id,
        master_id=master.id,
        business_id=business.id,
        size_length_cm=body.size_length_cm,
        size_height_cm=body.size_height_cm,
        complexity=body.complexity,
        # enum → .value: в БД лежит строка справочника, а не "TattooStyle.…"
        style=body.style.value,
        placement=body.placement.value,
    )
    session.add(work)
    await session.flush()

    tattoo_session = TattooSession(
        work_id=work.id,
        session_date=first_start,
        recommended_price=body.first_session.recommended_price,
        cost=body.first_session.cost,
        sketch_file_id=body.first_session.sketch_file_id,
        result_file_id=body.first_session.result_file_id,
        is_final_session=body.first_session.is_final_session,
    )
    session.add(tattoo_session)
    await session.flush()

    if tattoo_session.is_final_session:
        work.status = TattooWorkStatus.completed

    try:
        event_id = await _sync_session_to_calendar(tattoo_session, work, master, business)
        if event_id:
            tattoo_session.google_event_id = event_id
    except Exception:
        logger.exception("Failed to sync session %d to Google Calendar", tattoo_session.id)

    await session.commit()
    work = await _reload_work(session, work.id)
    _attach_file_urls(work.sessions)
    return TattooWorkResponse.model_validate(work)


@router.get("/works/{work_id}", response_model=TattooWorkResponse)
async def get_work(
    work_id: int,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
):
    """Получить детали тату-работы со всеми сеансами."""
    business, self_master = actor
    work = await _get_work_or_404(session, work_id, business, self_master)
    _attach_file_urls(work.sessions)
    return TattooWorkResponse.model_validate(work)


@router.patch("/works/{work_id}", response_model=TattooWorkResponse)
async def update_work(
    work_id: int,
    body: TattooWorkUpdate,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
):
    """Обновить поля тату-работы (без сеансов)."""
    business, self_master = actor
    work = await _get_work_or_404(session, work_id, business, self_master)

    update_data = body.model_dump(exclude_unset=True)
    if update_data.get("status") is not None and update_data["status"] not in (
        s.value for s in TattooWorkStatus
    ):
        raise HTTPException(status_code=422, detail="Недопустимый статус работы")
    # enum-справочники в БД хранятся строками-значениями
    for field in ("style", "placement"):
        if update_data.get(field):
            update_data[field] = update_data[field].value
    for field, value in update_data.items():
        setattr(work, field, value)

    await session.commit()

    # Параметры работы входят в summary/description событий календаря —
    # перезаливаем события всех сеансов, иначе календарь разойдётся с CRM
    master = await _resolve_master(session, business, self_master, work.master_id)
    work = await _reload_work(session, work_id)
    for tattoo_session in work.sessions:
        try:
            event_id = await _sync_session_to_calendar(tattoo_session, work, master, business)
            if event_id and event_id != tattoo_session.google_event_id:
                tattoo_session.google_event_id = event_id
        except Exception:
            logger.exception("Failed to re-sync session %d to Google Calendar", tattoo_session.id)
    await session.commit()
    work = await _reload_work(session, work_id)
    _attach_file_urls(work.sessions)
    return TattooWorkResponse.model_validate(work)


@router.delete("/works/{work_id}", status_code=204)
async def delete_work(
    work_id: int,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
):
    """Удалить тату-работу вместе со всеми сеансами и событиями из Google Calendar."""
    business, self_master = actor
    work = await _get_work_or_404(session, work_id, business, self_master)

    master = await _resolve_master(session, business, self_master, work.master_id)
    session_ids = [s.id for s in work.sessions]
    for tattoo_session in work.sessions:
        try:
            await _remove_session_from_calendar(tattoo_session, master, business)
        except Exception:
            logger.exception("Failed to remove session %d from Google Calendar", tattoo_session.id)

    await session.delete(work)
    await session.commit()
    files_storage.delete_session_dir(session_ids)


# ---------------------------------------------------------------------------
# TattooSession CRUD (вложены в TattooWork)
# ---------------------------------------------------------------------------


async def _get_session_or_404(
    session: SessionDep,
    work_id: int,
    session_id: int,
    business: Business,
    self_master: Master | None,
) -> tuple[TattooSession, TattooWork]:
    work = await _get_work_or_404(session, work_id, business, self_master)
    result = await session.execute(
        select(TattooSession).where(
            TattooSession.id == session_id,
            TattooSession.work_id == work.id,
        )
    )
    tattoo_session = result.scalar_one_or_none()
    if not tattoo_session:
        raise HTTPException(status_code=404, detail="Сеанс не найден")
    return tattoo_session, work


@router.post("/works/{work_id}/sessions", response_model=TattooSessionResponse, status_code=201)
async def create_session(
    work_id: int,
    body: TattooSessionCreate,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    force: bool = Query(False, description="Создать даже если время у мастера занято"),
):
    """
    Добавить новый сеанс к существующей тату-работе и отправить событие в Google Calendar.

    Если работа уже была завершена, новый сеанс возвращает её в статус in_progress
    (например, доработка/коррекция), если только сам не помечен как финальный.
    """
    business, self_master = actor
    work = await _get_work_or_404(session, work_id, business, self_master)
    master = await _resolve_master(session, business, self_master, work.master_id)

    new_start = _ensure_tz(body.session_date)
    if not force:
        await _assert_slot_free(
            session, business, master, new_start, new_start + _master_duration(master)
        )

    tattoo_session = TattooSession(
        work_id=work.id,
        session_date=new_start,
        recommended_price=body.recommended_price,
        cost=body.cost,
        sketch_file_id=body.sketch_file_id,
        result_file_id=body.result_file_id,
        is_final_session=body.is_final_session,
    )
    session.add(tattoo_session)
    await session.flush()

    work.status = (
        TattooWorkStatus.completed if body.is_final_session else TattooWorkStatus.in_progress
    )

    try:
        event_id = await _sync_session_to_calendar(tattoo_session, work, master, business)
        if event_id:
            tattoo_session.google_event_id = event_id
    except Exception:
        logger.exception("Failed to sync session %d to Google Calendar", tattoo_session.id)

    await session.commit()
    await session.refresh(tattoo_session, attribute_names=["files"])
    _attach_file_urls([tattoo_session])
    return TattooSessionResponse.model_validate(tattoo_session)


@router.patch("/works/{work_id}/sessions/{session_id}", response_model=TattooSessionResponse)
async def update_session(
    work_id: int,
    session_id: int,
    body: TattooSessionUpdate,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    force: bool = Query(False, description="Перенести даже если время у мастера занято"),
):
    """Обновить существующий сеанс и синхронизировать с Google Calendar."""
    business, self_master = actor
    tattoo_session, work = await _get_session_or_404(
        session, work_id, session_id, business, self_master
    )
    master = await _resolve_master(session, business, self_master, work.master_id)

    update_data = body.model_dump(exclude_unset=True)
    old_date = tattoo_session.session_date
    for field, value in update_data.items():
        setattr(tattoo_session, field, value)

    if not force and "session_date" in update_data:
        new_start = _ensure_tz(update_data["session_date"])
        if new_start != _ensure_tz(old_date):
            await _assert_slot_free(
                session,
                business,
                master,
                new_start,
                new_start + _master_duration(master),
                exclude_session_id=tattoo_session.id,
                # своё событие в календаре тоже переносится — не считать занятостью
                exclude_event_id=tattoo_session.google_event_id,
            )

    await session.flush()

    if "is_final_session" in update_data:
        work.status = (
            TattooWorkStatus.completed
            if tattoo_session.is_final_session
            else TattooWorkStatus.in_progress
        )

    try:
        event_id = await _sync_session_to_calendar(tattoo_session, work, master, business)
        if event_id:
            tattoo_session.google_event_id = event_id
    except Exception:
        logger.exception("Failed to sync session %d to Google Calendar", tattoo_session.id)

    await session.commit()
    # updated_at после UPDATE вычитывается отдельно: на SQLite server-onupdate
    # не приходит с UPDATE сам (Postgres тянет его через RETURNING)
    await session.refresh(tattoo_session, attribute_names=["files", "updated_at"])
    _attach_file_urls([tattoo_session])
    return TattooSessionResponse.model_validate(tattoo_session)


@router.delete("/works/{work_id}/sessions/{session_id}", status_code=204)
async def delete_session(
    work_id: int,
    session_id: int,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
):
    """Удалить сеанс и убрать событие из Google Calendar."""
    business, self_master = actor
    tattoo_session, _work = await _get_session_or_404(
        session, work_id, session_id, business, self_master
    )
    master = await _resolve_master(session, business, self_master, _work.master_id)

    try:
        await _remove_session_from_calendar(tattoo_session, master, business)
    except Exception:
        logger.exception("Failed to remove session %d from Google Calendar", tattoo_session.id)

    await session.delete(tattoo_session)
    await session.commit()
    files_storage.delete_session_dir([session_id])


# ---------------------------------------------------------------------------
# Фото сеансов (файлы на диске)
# ---------------------------------------------------------------------------


@router.post(
    "/works/{work_id}/sessions/{session_id}/files",
    response_model=TattooFileOut,
    status_code=201,
)
async def upload_session_file(
    work_id: int,
    session_id: int,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    kind: TattooFileKind = Form(..., description="sketch (эскиз) или result (результат)"),
    file: UploadFile = File(...),
) -> TattooFileOut:
    """Загрузить фото к сеансу:multipart, только image/*, до 10 МБ."""
    business, self_master = actor
    tattoo_session, _work = await _get_session_or_404(
        session, work_id, session_id, business, self_master
    )

    mime = file.content_type or ""
    if not mime.startswith("image/"):
        raise HTTPException(status_code=400, detail="Только изображения")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > files_storage.MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail="Файл больше 10 МБ")

    record = files_storage.save_file(
        session, tattoo_session, kind, data, file.filename or "photo", mime
    )
    await session.commit()
    await session.refresh(record)
    record.file_url = _file_url(record)
    return TattooFileOut.model_validate(record)


@router.get("/files/{file_id}")
async def get_session_file(
    file_id: int,
    session: SessionDep,
    user: CurrentUserOptionalDep,
    t: str | None = Query(default=None),
) -> FileResponse:
    """Отдать фото с диска.

    Два способа доступа (как у файлов чатов):
    - заголовки авторизации (fetch/blob в миниаппе);
    - подписанная ?t= — прямая ссылка на просмотр/скачивание вне страницы.
    """
    record = await session.get(TattooFile, file_id)
    if not record:
        raise HTTPException(status_code=404, detail="Файл не найден")
    if t is None or not verify_file_token(record.id, t):
        # Без валидного токена — обычная авторизация owner/master
        if user is None:
            raise HTTPException(status_code=401, detail="Authorization required")
        await get_master_business(user, session)

    path = files_storage.absolute_path(record)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден на диске")
    return FileResponse(
        path,
        media_type=record.mime,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(record.original_name)}",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/files/{file_id}", status_code=204)
async def delete_session_file(
    file_id: int,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
):
    """Удалить фото сеанса: строку в БД и файл на диске."""
    business, self_master = actor
    record = await session.get(TattooFile, file_id)
    if not record:
        raise HTTPException(status_code=404, detail="Файл не найден")
    # Скоупинг: фото должно принадлежать работе, доступной актору
    tattoo_session = await session.get(TattooSession, record.session_id)
    if tattoo_session is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    await _get_work_or_404(session, tattoo_session.work_id, business, self_master)

    await session.delete(record)
    await session.commit()
    files_storage.delete_file(record)
