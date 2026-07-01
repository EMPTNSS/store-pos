from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from pydantic import ValidationError
from sqlalchemy import or_
from sqlmodel import Session, select

from app.database import get_session
from app.models.product import Product
from app.schemas.cart import CartQuantity
from app.schemas.sale import SaleComplete
from app.services.cart import get_cart
from app.services.product_search import search_products
from app.services.sale import complete_sale

# Отдельный экземпляр Jinja-окружения с фильтром форматирования денег.
from fastapi.templating import Jinja2Templates

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _format_money(kopecks: int) -> str:
    """Копейки → строка вида '148.50' для отображения."""
    return f"{kopecks / 100:.2f}"


templates.env.filters["money"] = _format_money


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _cart_context(
    request: Request,
    error: Optional[str] = None,
    sale_result: Optional[dict] = None,
) -> dict:
    return {"cart": get_cart().view(), "error": error, "sale_result": sale_result}


@router.get("/cashier")
async def cashier_screen(request: Request):
    return _render(request, "cashier/index.html", _cart_context(request))


@router.get("/cashier/search")
async def search_items(
    request: Request,
    q: str = "",
    session: Session = Depends(get_session),
):
    """Поиск товара для чека по названию/коду/артикулу (разд. 3)."""
    results = search_products(session, q)
    return _render(
        request,
        "cashier/_search_results.html",
        {"results": results, "query": q.strip()},
    )


@router.post("/cashier/items")
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
        # Быстрый ввод/сканер: точное совпадение по числовому коду ИЛИ QR-коду.
        product = session.exec(
            select(Product).where(
                or_(Product.numeric_code == code, Product.qr_code == code)
            )
        ).first()
        if product is None:
            error = f"Товар с кодом {code} не найден"
        else:
            get_cart().add(product)
    return _render(request, "cashier/_cart.html", _cart_context(request, error))


@router.post("/cashier/items/{line_id}/quantity")
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
        if get_cart().set_quantity(line_id, data.quantity) is None:
            error = "Строка чека не найдена"
    return _render(request, "cashier/_cart.html", _cart_context(request, error))


@router.post("/cashier/items/{line_id}/delete")
async def delete_item(request: Request, line_id: int):
    get_cart().remove(line_id)
    return _render(request, "cashier/_cart.html", _cart_context(request))


@router.post("/cashier/clear")
async def clear_cart(request: Request):
    get_cart().clear()
    return _render(request, "cashier/_cart.html", _cart_context(request))


@router.post("/cashier/complete")
async def complete(
    request: Request,
    payment_method: str = Form(...),
    session: Session = Depends(get_session),
):
    """Завершить продажу: сохранить чек, списать остаток, записать движения (1.3)."""
    try:
        data = SaleComplete(payment_method=payment_method)
    except ValidationError:
        error = "Выберите способ оплаты"
        return _render(request, "cashier/_cart.html", _cart_context(request, error))

    try:
        receipt = complete_sale(session, get_cart(), data.payment_method)
    except ValueError as exc:
        return _render(request, "cashier/_cart.html", _cart_context(request, str(exc)))

    sale_result = {
        "number": receipt.receipt_number,
        "total": receipt.total,
        "payment_method": receipt.payment_method.value,
    }
    return _render(
        request, "cashier/_cart.html", _cart_context(request, sale_result=sale_result)
    )
