"""Открытие/закрытие рабочего дня (смены).

Тонкий роутер: дёрнуть сервис → вернуть фрагмент статуса смены для строки управления в
оболочке. Закрытие смены (макет 23.1) формирует документ завершения дня (23.2–23.5) одним
согласованным действием — через ``day_report_service.close_day_and_report`` (этап 7.1).
Инвариант «одна открытая смена» держит сервис.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.database import get_session
from app.models.day_report import DayReport
from app.services.day_report_service import close_day_and_report
from app.services.work_day_service import get_open_day, open_day

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _status_context(
    request: Request,
    session: Session,
    error: Optional[str] = None,
    report: Optional[DayReport] = None,
) -> dict:
    day = get_open_day(session)
    return {
        "request": request,
        "day_open": day is not None,
        "day": day,
        "error": error,
        "report": report,  # только что сформированный документ дня (после закрытия)
    }


def _render_status(
    request: Request,
    session: Session,
    error: Optional[str] = None,
    report: Optional[DayReport] = None,
):
    return templates.TemplateResponse(
        request,
        "shell/_day_status.html",
        _status_context(request, session, error, report),
    )


@router.post("/work-day/open")
async def open_work_day(request: Request, session: Session = Depends(get_session)):
    """Начать рабочий день. Если уже открыт — понятное сообщение об ошибке."""
    error = None
    try:
        open_day(session)
    except ValueError as exc:
        error = str(exc)
    return _render_status(request, session, error)


@router.post("/work-day/close")
async def close_work_day(request: Request, session: Session = Depends(get_session)):
    """Завершить рабочий день (макет 23.1) и сформировать документ дня (23.2–23.5).

    Закрытие смены и запись документа — одна транзакция (``close_day_and_report``, 7.1).
    """
    error = None
    report = None
    try:
        report = close_day_and_report(session)
    except ValueError as exc:
        error = str(exc)
    return _render_status(request, session, error, report)
