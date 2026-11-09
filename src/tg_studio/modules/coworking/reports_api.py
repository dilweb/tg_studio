"""API дашборда, отчётов и Excel владельца (07)."""

from datetime import datetime

from fastapi import APIRouter, Query, Response

from tg_studio.api.admin_deps import OwnerBusinessDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import Business
from tg_studio.modules.coworking import reports
from tg_studio.modules.coworking.report_excel import build_revenue_xlsx
from tg_studio.modules.coworking.report_schemas import (
    Dashboard,
    MastersReport,
    MoneyReport,
    RevenueReport,
    UpsellsReport,
    UsageReport,
)

router = APIRouter(tags=["coworking-reports"])


async def _facts(session, business: Business, from_: datetime, to: datetime):
    from_, to = reports.validate_period(from_, to)
    data = await reports.load_report_data(session, business, from_, to)
    revenue = reports.revenue_report(data, from_, to)
    return from_, to, data, revenue


@router.get("/dashboard", response_model=Dashboard)
async def owner_dashboard(session: SessionDep, business: OwnerBusinessDep):
    """Все виджеты главной владельца; фронтенд обновляет их polling'ом."""
    return await reports.dashboard(session, business)


@router.get("/reports/revenue", response_model=RevenueReport)
async def revenue_report(
    session: SessionDep, business: OwnerBusinessDep,
    from_: datetime = Query(..., alias="from"), to: datetime = Query(...),
):
    from_, to, data, _ = await _facts(session, business, from_, to)
    return reports.revenue_report(data, from_, to)


@router.get("/reports/usage", response_model=UsageReport)
async def usage_report(
    session: SessionDep, business: OwnerBusinessDep,
    from_: datetime = Query(..., alias="from"), to: datetime = Query(...),
):
    from_, to, data, _ = await _facts(session, business, from_, to)
    return await reports.usage_report(session, data, from_, to)


@router.get("/reports/money", response_model=MoneyReport)
async def money_report(
    session: SessionDep, business: OwnerBusinessDep,
    from_: datetime = Query(..., alias="from"), to: datetime = Query(...),
):
    from_, to, data, revenue = await _facts(session, business, from_, to)
    result = reports.money_report(data, from_, to, revenue)
    result["total_balance"] = reports.money(
        await reports.fill_balances(session, business.id, result["balances"])
    )
    return result


@router.get("/reports/masters", response_model=MastersReport)
async def masters_report(
    session: SessionDep, business: OwnerBusinessDep,
    from_: datetime = Query(..., alias="from"), to: datetime = Query(...),
):
    from_, to, data, _ = await _facts(session, business, from_, to)
    result = reports.masters_report(data, from_, to)
    await reports.fill_balances(session, business.id, result["masters"])
    return result


@router.get("/reports/upsells", response_model=UpsellsReport)
async def upsells_report(
    session: SessionDep, business: OwnerBusinessDep,
    from_: datetime = Query(..., alias="from"), to: datetime = Query(...),
):
    from_, to, data, _ = await _facts(session, business, from_, to)
    return reports.upsells_report(data, from_, to)


@router.get("/reports/revenue.xlsx")
async def revenue_excel(
    session: SessionDep, business: OwnerBusinessDep,
    from_: datetime = Query(..., alias="from"), to: datetime = Query(...),
):
    """XLSX с журналом операций и внешними id платежей для сверки с банком."""
    from_, to, data, revenue = await _facts(session, business, from_, to)
    money_data = reports.money_report(data, from_, to, revenue)
    content = build_revenue_xlsx(data, revenue, money_data, from_, to)
    filename = f"revenue_{from_.date()}_{to.date()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
