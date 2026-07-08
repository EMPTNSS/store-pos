"""Заявки на пополнение (этап 5.3, макет разд. 11).

Тонкий роутер: провалидировать вход → дёрнуть ``order_service`` → вернуть HTML-фрагмент.
Панель — вкладка рамы (2.5): кандидаты на заказ, открытые заявки по поставщикам, архив
закрытых. Форма выбора поставщика при пробросе — модалка внутри панели (HTMX-таргет
``#orders-modal``), по паттерну возвратов; рама (``shell/index.html``) не расширяется.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlmodel import Session

from app.database import get_session
from app.models.supplier import Supplier
from app.schemas.order import OrderLineInput
from app.services.money import format_money, format_quantity
from app.services.order_service import (
    add_manual_line,
    close_order,
    list_candidates,
    list_closed_orders,
    list_open_orders,
    push_to_order,
    remove_line,
    suggested_quantity,
    update_line,
)
from app.services.product_search import search_products
from app.services.product_service import get_product, product_suppliers
from app.services.supplier_service import list_active_suppliers, resolve_suppliers

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Единый формат денег и количества для интерфейса (как в products.py, app/services/money.py).
templates.env.filters["money"] = format_money
templates.env.filters["quantity"] = format_quantity

# Заголовок модалки при неоднозначном поставщике переиспользует HX-Retarget на #orders-modal.
_MODAL_HEADERS = {"HX-Retarget": "#orders-modal", "HX-Reswap": "innerHTML"}


def _render(request: Request, name: str, context: dict, status_code: int = 200, headers=None):
    return templates.TemplateResponse(
        request, name, context, status_code=status_code, headers=headers
    )


def panel_context(session: Session) -> dict:
    """Контекст тела панели «Заявки»: кандидаты, открытые заявки, архив."""
    return {
        "candidates": list_candidates(session),
        "open_orders": list_open_orders(session),
        "closed_orders": list_closed_orders(session),
    }


def _body(request: Request, session: Session):
    """Тело панели — общий ответ после любого изменения (цель ``#orders-body``)."""
    return _render(request, "orders/_body.html", panel_context(session))


def _push_form(request: Request, session: Session, product, *, preselected=None, error=None):
    """Форма проброса в модалку (выбор поставщика + нужное кол-во). Ретаргет на модалку."""
    context = {
        "product": product,
        "product_suppliers": product_suppliers(session, product.id),
        "preselected": preselected,
        "active_suppliers": list_active_suppliers(session),
        "suggested": suggested_quantity(product),
        "error": error,
    }
    headers = _MODAL_HEADERS if error else None
    return _render(request, "orders/_push_form.html", context, headers=headers)


@router.get("/orders/panel")
async def orders_panel(request: Request, session: Session = Depends(get_session)):
    """Полная панель раздела «Заявки» (загружается в раму по HTMX один раз)."""
    return _render(request, "orders/_panel.html", panel_context(session))


@router.get("/orders/push-form")
async def push_form_open(
    request: Request,
    product_id: int,
    supplier_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    """Открыть форму проброса кандидата. supplier_id задан — поставщик предвыбран (группа)."""
    product = get_product(session, product_id)
    if product is None:
        return HTMLResponse("Товар не найден", status_code=404)
    preselected = session.get(Supplier, supplier_id) if supplier_id is not None else None
    return _push_form(request, session, product, preselected=preselected)


@router.post("/orders/push")
async def push(
    request: Request,
    product_id: int = Form(...),
    supplier_id: Optional[str] = Form(None),
    supplier_name: Optional[str] = Form(None),
    needed_quantity: str = Form(...),
    comment: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    """Проброс кандидата в заявку (макет 11.9.5). Поставщик выбирается всегда."""
    product = get_product(session, product_id)
    if product is None:
        return HTMLResponse("Товар не найден", status_code=404)

    # Разрешить поставщика: явный id → существующий; имя → реюз/инлайн-создание; иначе —
    # если у товара ровно один поставщик, берём его; при 0 или нескольких — просим выбрать.
    resolved_id: Optional[int] = None
    if supplier_id and supplier_id.strip():
        resolved_id = int(supplier_id)
    elif supplier_name and supplier_name.strip():
        resolved_id = resolve_suppliers([supplier_name], session)[0].id
    else:
        suppliers = product_suppliers(session, product_id)
        if len(suppliers) == 1:
            resolved_id = suppliers[0].id
        else:
            return _push_form(
                request, session, product, error="Выберите поставщика для заявки"
            )

    try:
        data = OrderLineInput(needed_quantity=needed_quantity, comment=comment)
    except ValidationError:
        preselected = session.get(Supplier, resolved_id)
        return _push_form(
            request,
            session,
            product,
            preselected=preselected,
            error="Нужное количество должно быть больше 0",
        )

    push_to_order(session, product_id, resolved_id, data.needed_quantity, data.comment)
    return _body(request, session)


@router.get("/orders/{order_id}/search")
async def order_search(
    request: Request,
    order_id: int,
    q: str = "",
    session: Session = Depends(get_session),
):
    """Поиск товара для ручного добавления в заявку (макет 11.6). Тот же движок, что на кассе."""
    results = search_products(session, q)
    return _render(
        request,
        "orders/_add_search_results.html",
        {"results": results, "order_id": order_id, "query": q.strip()},
    )


@router.post("/orders/{order_id}/lines")
async def add_line_route(
    request: Request,
    order_id: int,
    product_id: int = Form(...),
    session: Session = Depends(get_session),
):
    """Добавить произвольный товар в открытую заявку (макет 11.6)."""
    try:
        add_manual_line(session, order_id, product_id)
    except ValueError:
        pass  # закрытая/несуществующая заявка — просто перерисовать актуальное тело
    return _body(request, session)


@router.post("/orders/lines/{line_id}/update")
async def update_line_route(
    request: Request,
    line_id: int,
    needed_quantity: str = Form(...),
    comment: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    """Изменить нужное количество / примечание строки (макет 11.6)."""
    try:
        data = OrderLineInput(needed_quantity=needed_quantity, comment=comment)
    except ValidationError:
        return _body(request, session)  # невалидное кол-во игнорируем, тело актуально
    try:
        update_line(session, line_id, data.needed_quantity, data.comment)
    except ValueError:
        pass
    return _body(request, session)


@router.post("/orders/lines/{line_id}/delete")
async def delete_line_route(
    request: Request,
    line_id: int,
    session: Session = Depends(get_session),
):
    """Удалить строку из открытой заявки (макет 11.6)."""
    try:
        remove_line(session, line_id)
    except ValueError:
        pass
    return _body(request, session)


@router.post("/orders/{order_id}/close")
async def close_order_route(
    request: Request,
    order_id: int,
    session: Session = Depends(get_session),
):
    """Закрыть заявку при заказе (макет 11.7). Склад не трогается, заявка уходит в архив."""
    try:
        close_order(session, order_id)
    except ValueError:
        pass
    return _body(request, session)
