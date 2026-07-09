"""Ручной приём накладной (этап 6.1, макет разд. 12.3).

Приём через поиск карточек: товар найден → приход (кол-во + обе цены), не найден →
создание карточки нового товара. Складская/ценовая логика — в product_service; здесь
только веб-обвязка и HTMX-фрагменты для панели «Добавить».

Валидация на границе (CLAUDE.md правило 3): вход проверяется схемой до попадания в
сервис. Ошибка схемы → 422 + перерисовка формы с сообщениями (паттерн products-роутов).
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.database import get_session
from app.models.product import UnitEnum
from app.schemas.product import ProductCreate, ProductReceive
from app.services.money import format_money, format_quantity
from app.services.product_search import search_products
from app.services.product_service import (
    create_product,
    get_product,
    is_low_stock,
    receive_product,
)
from app.services.supplier_service import list_active_suppliers

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Единый формат денег/количества для форм приёма (как в products.py), см. app/services/money.py.
templates.env.filters["money"] = format_money
templates.env.filters["quantity"] = format_quantity


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _errors(exc: ValidationError) -> list[str]:
    return [err["msg"].removeprefix("Value error, ") for err in exc.errors()]


@router.get("/receiving/search")
async def search_for_receiving(
    request: Request, q: str = "", session: Session = Depends(get_session)
):
    """Поиск товара для приёма (макет 12.3). Тот же движок, что на кассе/в «Товарах».

    Флаг low_stock (5.2) считаем в роуте, чтобы не дублировать критерий минимума в шаблоне.
    """
    results = [(p, is_low_stock(p)) for p in search_products(session, q)]
    return _render(
        request,
        "receiving/_search_results.html",
        {"results": results, "query": q.strip()},
    )


@router.get("/receiving/new")
async def create_form(
    request: Request, name: str = "", session: Session = Depends(get_session)
):
    """Форма создания карточки нового товара (ветка «товара нет»). Имя предзаполнено запросом."""
    return _render(
        request,
        "receiving/_create_form.html",
        {
            "units": list(UnitEnum),
            "suppliers": list_active_suppliers(session),
            "errors": [],
            "values": {"name": name} if name else {},
            "created_code": None,
        },
    )


@router.post("/receiving/create")
async def create(
    request: Request,
    name: str = Form(...),
    price_buy: str = Form(...),
    price_sell: str = Form(...),
    quantity: str = Form(...),
    unit: str = Form(...),
    article: Optional[str] = Form(None),
    min_stock: Optional[str] = Form(None),
    qr_code: Optional[str] = Form(None),
    supplier: list[str] = Form(default=[]),
    session: Session = Depends(get_session),
):
    """Создать новый товар из приёма. Переиспользует ProductCreate/create_product (0.3)."""
    supplier_names = [s.strip() for s in supplier if s and s.strip()]
    values = {
        "name": name,
        "price_buy": price_buy,
        "price_sell": price_sell,
        "quantity": quantity,
        "unit": unit,
        "article": article or "",
        "min_stock": min_stock or "",
        "qr_code": qr_code or "",
        "supplier_names": supplier_names,
    }

    def _fail(errors: list[str]):
        return _render(
            request,
            "receiving/_create_form.html",
            {
                "units": list(UnitEnum),
                "suppliers": list_active_suppliers(session),
                "errors": errors,
                "values": values,
                "created_code": None,
            },
            status_code=422,
        )

    try:
        data = ProductCreate(
            name=name,
            price_buy=price_buy,
            price_sell=price_sell,
            quantity=quantity,
            unit=unit,
            article=(article or "").strip() or None,
            min_stock=(min_stock or "").strip() or "0",
            qr_code=(qr_code or "").strip() or None,
            supplier_names=supplier_names,
        )
    except ValidationError as exc:
        return _fail(_errors(exc))

    try:
        product = create_product(data, session)
    except IntegrityError:
        session.rollback()
        return _fail(["QR-код уже используется другим товаром"])

    return _render(
        request,
        "receiving/_create_form.html",
        {"created_code": product.numeric_code},
    )


# Динамические маршруты по product_id — ПОСЛЕ статических /receiving/new и
# /receiving/create, иначе «create»/«new» ловятся как product_id и падают на int-разборе.
@router.get("/receiving/{product_id}/form")
async def receive_form(
    request: Request, product_id: int, session: Session = Depends(get_session)
):
    """Форма приёма существующего товара: кол-во + обе цены (предзаполнены текущими)."""
    product = get_product(session, product_id)
    if product is None:
        return HTMLResponse("Товар не найден", status_code=404)
    return _render(
        request, "receiving/_receive_form.html", {"product": product, "values": {}}
    )


@router.post("/receiving/{product_id}")
async def receive(
    request: Request,
    product_id: int,
    received_quantity: str = Form(...),
    price_buy: Optional[str] = Form(None),
    price_sell: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    """Оприходовать партию (макет 12.3): += кол-во, движение «приход», при смене цены — история."""
    product = get_product(session, product_id)
    if product is None:
        return HTMLResponse("Товар не найден", status_code=404)

    values = {
        "received_quantity": received_quantity,
        "price_buy": price_buy or "",
        "price_sell": price_sell or "",
    }
    try:
        data = ProductReceive(
            received_quantity=received_quantity,
            price_buy=price_buy,
            price_sell=price_sell,
        )
    except ValidationError as exc:
        return _render(
            request,
            "receiving/_receive_form.html",
            {"product": product, "values": values, "errors": _errors(exc)},
            status_code=422,
        )

    updated = receive_product(
        session,
        product_id,
        data.received_quantity,
        price_buy=data.price_buy,
        price_sell=data.price_sell,
    )
    return _render(request, "receiving/_received.html", {"product": updated})
