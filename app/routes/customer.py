"""Экран покупателя (этап 2.3, макет разд. 20).

Второе окно, обращённое к покупателю. Пассивно зеркалит кассу: читает живую
корзину-синглтон и маркер последней продажи, ничего не меняя. Обновляется
HTMX-опросом (``/customer/state`` тянется каждые ``customer_display_poll_ms``).
"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.services.cart import get_cart
from app.services.customer_display import get_last_sale, resolve_display_state
from app.services.money import format_money, format_quantity

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Тот же формат денег/количества, что на кассе и в печати (app/services/money.py).
templates.env.filters["money"] = format_money
templates.env.filters["quantity"] = format_quantity


def _state_context() -> dict:
    state = resolve_display_state(
        get_cart().view(),
        get_last_sale(),
        datetime.now(),
        settings.customer_display_thanks_seconds,
    )
    return {
        "state": state,
        "welcome": settings.customer_display_welcome,
        "thanks_text": settings.customer_display_thanks_text,
    }


@router.get("/customer")
async def customer_screen(request: Request):
    """Полная страница экрана покупателя с контейнером опроса."""
    context = _state_context()
    context["poll_ms"] = settings.customer_display_poll_ms
    return templates.TemplateResponse(request, "customer/index.html", context)


@router.get("/customer/state")
async def customer_state(request: Request):
    """Текущее состояние экрана (фрагмент, который тянет опрос)."""
    return templates.TemplateResponse(request, "customer/_display.html", _state_context())
