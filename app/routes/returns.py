"""Возврат товара (этап 4.1, макет разд. 14).

Тонкий роутер: провалидировать вход → дёрнуть корзину/сервис → вернуть HTML-фрагмент.
Логика — в ``return_cart``/``return_service``. Зеркалит ``/cashier/*`` (1.1–1.3), но с
редактируемой ценой и без округления итога вверх (возврат — ровно указанная сумма).
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import or_
from sqlmodel import Session, select

from app.database import get_session
from app.models.product import Product
from app.schemas.cart import CartQuantity
from app.schemas.return_ import ReturnComplete, ReturnLinePrice
from app.services.money import format_money
from app.services.product_search import search_products
from app.services.return_cart import get_return_cart
from app.services.return_service import complete_return

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Единый формат денег для интерфейса (как в cashier.py/shell.py, см. app/services/money.py).
templates.env.filters["money"] = format_money


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _cart_context(
    request: Request,
    error: Optional[str] = None,
    return_result: Optional[dict] = None,
) -> dict:
    return {
        "cart": get_return_cart().view(),
        "error": error,
        "return_result": return_result,
    }


@router.get("/returns/modal")
async def return_modal(request: Request):
    """Тело модалки возврата: поиск + текущий черновик корзины возврата."""
    return _render(request, "returns/_modal.html", _cart_context(request))


@router.get("/returns/search")
async def search_items(
    request: Request,
    q: str = "",
    session: Session = Depends(get_session),
):
    """Поиск товара для возврата — тот же движок, что на кассе (разд. 3)."""
    results = search_products(session, q)
    return _render(
        request,
        "returns/_search_results.html",
        {"results": results, "query": q.strip()},
    )


@router.post("/returns/items")
async def add_item(
    request: Request,
    numeric_code: str = Form(...),
    session: Session = Depends(get_session),
):
    code = numeric_code.strip()
    error = None
    if not code:
        error = "Введите числовой код товара"
    else:
        # Сканер/быстрый ввод: точное совпадение по числовому коду ИЛИ QR-коду.
        product = session.exec(
            select(Product).where(
                or_(Product.numeric_code == code, Product.qr_code == code)
            )
        ).first()
        if product is None:
            error = f"Товар с кодом {code} не найден"
        else:
            get_return_cart().add(product)
    return _render(request, "returns/_return_cart.html", _cart_context(request, error))


@router.post("/returns/items/{line_id}/quantity")
async def change_quantity(
    request: Request,
    line_id: int,
    quantity: str = Form(...),
):
    error = None
    try:
        data = CartQuantity(quantity=quantity)
    except ValidationError:
        error = "Количество должно быть числом больше 0"
    else:
        if get_return_cart().set_quantity(line_id, data.quantity) is None:
            error = "Строка возврата не найдена"
    return _render(request, "returns/_return_cart.html", _cart_context(request, error))


@router.post("/returns/items/{line_id}/price")
async def change_price(
    request: Request,
    line_id: int,
    price: str = Form(...),
):
    error = None
    try:
        data = ReturnLinePrice(price=price)
    except ValidationError:
        error = "Цена должна быть числом не меньше 0"
    else:
        if get_return_cart().set_price(line_id, data.price) is None:
            error = "Строка возврата не найдена"
    return _render(request, "returns/_return_cart.html", _cart_context(request, error))


@router.post("/returns/items/{line_id}/delete")
async def delete_item(request: Request, line_id: int):
    get_return_cart().remove(line_id)
    return _render(request, "returns/_return_cart.html", _cart_context(request))


@router.post("/returns/clear")
async def clear_cart(request: Request):
    """Очистить черновик возврата (отмена/закрытие модалки, разовое действие 2.5)."""
    get_return_cart().clear()
    return _render(request, "returns/_return_cart.html", _cart_context(request))


@router.post("/returns/complete")
async def complete(
    request: Request,
    payment_method: str = Form(...),
    session: Session = Depends(get_session),
):
    """Оформить возврат: сохранить чек, вернуть остаток, записать движения (разд. 14)."""
    try:
        data = ReturnComplete(payment_method=payment_method)
    except ValidationError:
        error = "Выберите способ возврата"
        return _render(request, "returns/_return_cart.html", _cart_context(request, error))

    try:
        receipt = complete_return(session, get_return_cart(), data.payment_method)
    except ValueError as exc:
        return _render(
            request, "returns/_return_cart.html", _cart_context(request, str(exc))
        )

    return_result = {
        "number": receipt.return_number,
        "total": receipt.total,
        "payment_method": receipt.payment_method.value,
    }
    return _render(
        request,
        "returns/_return_cart.html",
        _cart_context(request, return_result=return_result),
    )
