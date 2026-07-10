import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from pydantic import ValidationError
from sqlalchemy import or_
from sqlmodel import Session, select

from app.database import get_session
from app.hardware.invoice_printer import get_invoice_printer
from app.hardware.receipt_printer import get_receipt_printer
from app.models.product import Product
from app.models.receipt import Receipt, ReceiptLine
from app.schemas.cart import CartQuantity
from app.schemas.sale import SaleComplete
from app.services.cart import get_cart
from app.services.customer_display import mark_sale_completed
from app.services.invoice_render import render_invoice_text
from app.services.money import format_money
from app.services.product_search import search_products
from app.services.receipt_render import render_receipt_text
from app.services.sale import complete_sale
from app.services.work_day_service import get_open_day

# Отдельный экземпляр Jinja-окружения с фильтром форматирования денег.
from fastapi.templating import Jinja2Templates

log = logging.getLogger(__name__)

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Единый формат денег для интерфейса и печати (см. app/services/money.py).
templates.env.filters["money"] = format_money


def _print_receipt(session: Session, receipt: Receipt) -> bool:
    """Best-effort печать сохранённого чека. Сбой не откатывает продажу.

    Печатаем снимок из БД (``ReceiptLine``), а не корзину — она уже очищена.
    Возвращает True, если печать прошла без ошибки.
    """
    try:
        lines = session.exec(
            select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id)
        ).all()
        text = render_receipt_text(receipt, list(lines))
        get_receipt_printer().print(receipt.receipt_number, text)
    except Exception:  # noqa: BLE001 — продажа уже зафиксирована, печать вторична
        log.exception("Печать чека №%s не удалась", receipt.receipt_number)
        return False
    return True


def _print_invoice(session: Session, receipt: Receipt) -> bool:
    """Best-effort формирование накладной из сохранённого чека (макет 1.4, 18.5).

    Независимо от печати чека: свой ``try/except``. Сбой не откатывает продажу и не
    мешает печати чека. Возвращает True, если накладная сформирована без ошибки.
    """
    try:
        lines = session.exec(
            select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id)
        ).all()
        text = render_invoice_text(receipt, list(lines))
        get_invoice_printer().print(receipt.receipt_number, text)
    except Exception:  # noqa: BLE001 — продажа уже зафиксирована, накладная вторична
        log.exception("Формирование накладной чека №%s не удалось", receipt.receipt_number)
        return False
    return True


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _cart_context(
    request: Request,
    session: Session,
    error: Optional[str] = None,
    sale_result: Optional[dict] = None,
) -> dict:
    # day_open управляет показом формы завершения в _cart.html: без открытой смены
    # вместо кнопки «Завершить продажу» рендерится заметка (guard дублируется на сервисе).
    return {
        "cart": get_cart().view(),
        "error": error,
        "sale_result": sale_result,
        "day_open": get_open_day(session) is not None,
    }


@router.get("/cashier")
async def cashier_screen(request: Request, session: Session = Depends(get_session)):
    return _render(request, "cashier/index.html", _cart_context(request, session))


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
    return _render(request, "cashier/_cart.html", _cart_context(request, session, error))


@router.post("/cashier/items/{line_id}/quantity")
async def change_quantity(
    request: Request,
    line_id: int,
    quantity: str = Form(...),
    session: Session = Depends(get_session),
):
    error = None
    try:
        data = CartQuantity(quantity=quantity)
    except ValidationError:
        error = "Количество должно быть числом больше 0"
    else:
        if get_cart().set_quantity(line_id, data.quantity) is None:
            error = "Строка чека не найдена"
    return _render(request, "cashier/_cart.html", _cart_context(request, session, error))


@router.post("/cashier/items/{line_id}/delete")
async def delete_item(
    request: Request, line_id: int, session: Session = Depends(get_session)
):
    get_cart().remove(line_id)
    return _render(request, "cashier/_cart.html", _cart_context(request, session))


@router.post("/cashier/clear")
async def clear_cart(request: Request, session: Session = Depends(get_session)):
    get_cart().clear()
    return _render(request, "cashier/_cart.html", _cart_context(request, session))


@router.post("/cashier/complete")
async def complete(
    request: Request,
    payment_method: str = Form(...),
    print_invoice: bool = Form(False),
    session: Session = Depends(get_session),
):
    """Завершить продажу: сохранить чек, списать остаток, записать движения (1.3)."""
    try:
        data = SaleComplete(payment_method=payment_method, print_invoice=print_invoice)
    except ValidationError:
        error = "Выберите способ оплаты"
        return _render(request, "cashier/_cart.html", _cart_context(request, session, error))

    try:
        receipt = complete_sale(session, get_cart(), data.payment_method)
    except ValueError as exc:
        return _render(request, "cashier/_cart.html", _cart_context(request, session, str(exc)))

    # Отметить продажу для экрана покупателя (2.3): благодарность после очистки корзины.
    # Best-effort и вне транзакции — продажа уже зафиксирована, экран вторичен.
    try:
        mark_sale_completed(receipt)
    except Exception:  # noqa: BLE001 — экран покупателя не должен ломать продажу
        log.exception("Не удалось отметить продажу №%s для экрана покупателя",
                      receipt.receipt_number)

    # Продажа зафиксирована — печатаем чек (best-effort, сбой не откатывает продажу).
    printed = _print_receipt(session, receipt)

    # Накладная — опционально и после чека (макет 1.4), независимый best-effort шаг.
    invoice_printed = None
    if data.print_invoice:
        invoice_printed = _print_invoice(session, receipt)

    sale_result = {
        "number": receipt.receipt_number,
        "total": receipt.total,
        "payment_method": receipt.payment_method.value,
        "printed": printed,
        "invoice_requested": data.print_invoice,
        "invoice_printed": invoice_printed,
    }
    return _render(
        request,
        "cashier/_cart.html",
        _cart_context(request, session, sale_result=sale_result),
    )
