"""Навигационная оболочка (этап 2.5).

Рама вкладок в одном окне pywebview: касса — постоянная стартовая вкладка,
рабочие разделы (Товары, Заявки, Добавить, Чеки за день) открываются во вкладки-заглушки.
Здесь только презентация рамы и панелей-заглушек — никакой денежной/складской логики.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.cart import get_cart
from app.services.money import format_money

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Рама встраивает шаблон кассы (_screen.html → _cart.html), который использует фильтр
# денег. Регистрируем тот же формат, что на кассе и в печати (app/services/money.py).
templates.env.filters["money"] = format_money

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
async def shell_screen(request: Request):
    """Страница-рама: ряд вкладок + панель кассы, встроенная на сервере.

    Встроенный шаблон кассы (`cashier/_cart.html`) читает живую корзину — передаём тот же
    контекст, что standalone-роут `/cashier`. Логика корзины/продажи не трогается.
    """
    return templates.TemplateResponse(
        request,
        "shell/index.html",
        {
            "sections": SECTIONS,
            "cart": get_cart().view(),
            "error": None,
            "sale_result": None,
        },
    )


@router.get("/panels/{key}")
async def section_panel(request: Request, key: str):
    """Фрагмент раздела, подгружается в панель по HTMX один раз.

    «Товары» — реальная панель поиска для карточки (этап 3.1). Остальные разделы —
    заглушки до своих этапов (Заявки — 5.3, Добавить — 6, Чеки — 7).
    """
    title = SECTIONS.get(key)
    if title is None:
        return HTMLResponse("Раздел не найден", status_code=404)
    if key == "products":
        return templates.TemplateResponse(request, "products/_panel.html", {})
    return templates.TemplateResponse(
        request,
        "shell/_stub.html",
        # Флаг test_field — только у «Добавить»: тестовое поле для проверки сохранности
        # состояния при переключении вкладок (уйдёт на этапе 6).
        {"key": key, "title": title, "test_field": key == "add"},
    )
