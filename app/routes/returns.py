"""Возврат товара (этап 4.1, макет разд. 14).

Тонкий роутер: провалидировать вход → дёрнуть корзину/сервис → вернуть HTML-фрагмент.
Логика — в ``return_cart``/``return_service``. Зеркалит ``/cashier/*`` (1.1–1.3), но с
редактируемой ценой и без округления итога вверх (возврат — ровно указанная сумма).
"""

from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import or_
from sqlmodel import Session, select

from app.database import get_session
from app.models.product import Product
from app.models.receipt import Receipt, ReceiptLine
from app.schemas.cart import CartQuantity
from app.schemas.return_ import (
    ReceiptLookup,
    ReturnComplete,
    ReturnFromReceipt,
    ReturnLinePrice,
)
from app.services.money import format_money
from app.services.product_search import search_products
from app.services.return_cart import get_return_cart
from app.services.return_service import (
    already_returned,
    complete_return,
    returnable_lines,
)

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


@router.get("/returns/receipt")
async def receipt_lookup(
    request: Request,
    number: str = "",
    session: Session = Depends(get_session),
):
    """Найти завершённый чек по номеру и показать строки read-only (4.2, разд. 15.2–15.3)."""
    num = number.strip()
    error = None
    receipt = None
    lines = []
    if not num:
        error = "Введите номер чека"
    else:
        try:
            data = ReceiptLookup(number=num)
        except ValidationError:
            error = "Номер чека — целое число больше 0"
        else:
            receipt = session.exec(
                select(Receipt).where(Receipt.receipt_number == data.number)
            ).first()
            if receipt is None:
                error = f"Чек №{num} не найден"
            else:
                lines = returnable_lines(session, receipt)
    return _render(
        request,
        "returns/_receipt_lookup.html",
        {"receipt": receipt, "lines": lines, "error": error, "query": num},
    )


@router.post("/returns/from-receipt")
async def add_from_receipt(
    request: Request,
    source_line_id: str = Form(...),
    quantity: str = Form("1"),
    session: Session = Depends(get_session),
):
    """Перенести строку завершённого чека в возврат (корректирующий возврат, 4.2)."""
    error = None
    try:
        data = ReturnFromReceipt(source_line_id=source_line_id, quantity=quantity)
    except ValidationError:
        error = "Проверьте строку чека и количество"
        return _render(request, "returns/_return_cart.html", _cart_context(request, error))

    receipt_line = session.get(ReceiptLine, data.source_line_id)
    if receipt_line is None:
        error = "Строка чека не найдена"
        return _render(request, "returns/_return_cart.html", _cart_context(request, error))

    cart = get_return_cart()
    # Доступно = продано − уже возвращено (в БД) − уже набрано в черновике по этой строке.
    in_cart = sum(
        (line.quantity for line in cart.view().lines if line.source_line_id == receipt_line.id),
        Decimal("0"),
    )
    available = receipt_line.quantity - already_returned(session, receipt_line.id) - in_cart
    if data.quantity > available:
        error = f"Доступно к возврату: {available} {receipt_line.unit.value}"
        return _render(request, "returns/_return_cart.html", _cart_context(request, error))

    try:
        cart.add_from_receipt_line(receipt_line, data.quantity)
    except ValueError as exc:
        error = str(exc)
    return _render(request, "returns/_return_cart.html", _cart_context(request, error))


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
        try:
            if get_return_cart().set_price(line_id, data.price) is None:
                error = "Строка возврата не найдена"
        except ValueError as exc:
            # Корректирующая строка (4.2): цена по чеку зафиксирована.
            error = str(exc)
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

    # Корректирующий возврат (4.2): в баннере ссылаемся на чек продажи-первоисточник.
    source_number = None
    if receipt.source_receipt_id is not None:
        source = session.get(Receipt, receipt.source_receipt_id)
        source_number = source.receipt_number if source is not None else None

    return_result = {
        "number": receipt.return_number,
        "total": receipt.total,
        "payment_method": receipt.payment_method.value,
        "source_number": source_number,
    }
    return _render(
        request,
        "returns/_return_cart.html",
        _cart_context(request, return_result=return_result),
    )
