"""Открытие/закрытие рабочего дня (смены).

Тонкий роутер: дёрнуть ``work_day_service`` → вернуть фрагмент статуса смены для строки
управления в оболочке. Итоговый документ завершения дня (макет 23.2–23.5) здесь НЕ
формируется — это этап 7.1. Инвариант «одна открытая смена» держит сервис.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.database import get_session
from app.services.work_day_service import close_day, get_open_day, open_day

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _status_context(request: Request, session: Session, error: Optional[str] = None) -> dict:
    day = get_open_day(session)
    return {"request": request, "day_open": day is not None, "day": day, "error": error}


def _render_status(request: Request, session: Session, error: Optional[str] = None):
    return templates.TemplateResponse(
        "shell/_day_status.html", _status_context(request, session, error)
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
    """Завершить рабочий день (макет 23.1). Документ дня (23.2–23.5) — этап 7.1, не здесь."""
    error = None
    try:
        close_day(session)
    except ValueError as exc:
        error = str(exc)
    return _render_status(request, session, error)
