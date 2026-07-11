"""Навигационная оболочка (этап 2.5).

Рама вкладок в одном окне pywebview: касса — постоянная стартовая вкладка,
рабочие разделы (Товары, Заявки, Добавить, Чеки за день) открываются во вкладки-заглушки.
Здесь только презентация рамы и панелей-заглушек — никакой денежной/складской логики.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.database import get_session
from app.services.cart import get_cart
from app.services.money import format_money, format_quantity
from app.services.day_report_service import day_report_lines, list_day_reports
from app.services.order_service import (
    list_candidates,
    list_closed_orders,
    list_open_orders,
)
from app.services.work_day_service import get_open_day

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Рама встраивает шаблон кассы (_screen.html → _cart.html), который использует фильтр
# денег. Регистрируем тот же формат, что на кассе и в печати (app/services/money.py).
# Фильтр количества нужен панели «Заявки» (5.3), встраиваемой через section_panel.
templates.env.filters["money"] = format_money
templates.env.filters["quantity"] = format_quantity

# Белый список разделов рамы: key (англ., для id/URL) → заголовок вкладки (рус.).
# Наполнение приходит на своих этапах (Товары/Заявки — 3/5.3, Добавить — 6, Чеки — 7),
# сейчас все four — пустые панели-заглушки.
SECTIONS: dict[str, str] = {
    "products": "Товары",
    "orders": "Заявки",
    "add": "Добавить",
    "receipts": "Чеки за день",
}


@router.get("/")
async def shell_screen(request: Request, session: Session = Depends(get_session)):
    """Страница-рама: ряд вкладок + панель кассы, встроенная на сервере.

    Встроенный шаблон кассы (`cashier/_cart.html`) читает живую корзину — передаём тот же
    контекст, что standalone-роут `/cashier`. Логика корзины/продажи не трогается.
    ``day_open`` управляет строкой смены и показом формы завершения (блокировка кассы 7.1-prep).
    """
    return templates.TemplateResponse(
        request,
        "shell/index.html",
        {
            "sections": SECTIONS,
            "cart": get_cart().view(),
            "error": None,
            "sale_result": None,
            "day_open": get_open_day(session) is not None,
        },
    )


@router.get("/panels/{key}")
async def section_panel(
    request: Request, key: str, session: Session = Depends(get_session)
):
    """Фрагмент раздела, подгружается в панель по HTMX один раз.

    «Товары» — реальная панель поиска для карточки (этап 3.1). «Заявки» — панель
    пополнения (этап 5.3). «Добавить» — панель ручного приёма накладной (этап 6.1).
    «Чеки за день» — документы завершения дня (этап 7.1).
    """
    title = SECTIONS.get(key)
    if title is None:
        return HTMLResponse("Раздел не найден", status_code=404)
    if key == "products":
        return templates.TemplateResponse(request, "products/_panel.html", {})
    if key == "orders":
        return templates.TemplateResponse(
            request,
            "orders/_panel.html",
            {
                "candidates": list_candidates(session),
                "open_orders": list_open_orders(session),
                "closed_orders": list_closed_orders(session),
            },
        )
    if key == "add":
        return templates.TemplateResponse(request, "add/_panel.html", {})
    if key == "receipts":
        # «Чеки за день» — документы завершения дня (7.1). Свежий документ (наверху списка)
        # разворачиваем сразу, чтобы после закрытия смены его было видно без лишнего клика.
        reports = list_day_reports(session)
        current = reports[0] if reports else None
        return templates.TemplateResponse(
            request,
            "reports/_panel.html",
            {
                "reports": reports,
                "report": current,
                "lines": day_report_lines(session, current.id) if current else [],
            },
        )
    return templates.TemplateResponse(
        request,
        "shell/_stub.html",
        {"key": key, "title": title, "test_field": False},
    )
