"""Просмотр сохранённых документов завершения дня (этап 7.1, макет разд. 23.5).

Формирование документа происходит при закрытии смены (``POST /work-day/close`` →
``day_report_service.close_day_and_report``). Здесь только чтение: список сохранённых
документов и просмотр одного — фрагменты для секции «Чеки за день» (рама 2.5).
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.database import get_session
from app.services.day_report_service import (
    day_report_lines,
    get_day_report,
    list_day_reports,
)
from app.services.money import format_money, format_quantity

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Единый формат денег и количества (как в других роутерах, app/services/money.py).
templates.env.filters["money"] = format_money
templates.env.filters["quantity"] = format_quantity


@router.get("/day/reports")
async def reports_list(request: Request, session: Session = Depends(get_session)):
    """Список сохранённых документов дня (фрагмент для #reports-list)."""
    reports = list_day_reports(session)
    return templates.TemplateResponse(
        request,
        "reports/_list.html",
        {"reports": reports, "report": reports[0] if reports else None},
    )


@router.get("/day/reports/{report_id}")
async def report_view(
    request: Request, report_id: int, session: Session = Depends(get_session)
):
    """Просмотр одного сохранённого документа (фрагмент для #report-view)."""
    report = get_day_report(session, report_id)
    if report is None:
        return HTMLResponse("Документ не найден", status_code=404)
    return templates.TemplateResponse(
        request,
        "reports/_document.html",
        {"report": report, "lines": day_report_lines(session, report_id)},
    )
