from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.database import get_session
from app.models.product import ProductStatus, UnitEnum
from app.schemas.product import ProductCreate, ProductEdit, QuantityAdjust
from app.services.money import format_money, format_quantity
from app.services.product_search import search_products
from app.services.product_service import (
    adjust_quantity,
    build_price_chart,
    create_product,
    get_product,
    is_low_stock,
    price_history,
    product_movements,
    product_stats,
    product_view,
    update_product,
)
from app.services.supplier_service import list_active_suppliers

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Единый формат денег и количества для интерфейса (карточка/поиск), см. app/services/money.py.
templates.env.filters["money"] = format_money
templates.env.filters["quantity"] = format_quantity  # без хвостовых нулей: «12», «1.5»


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _card_context(session: Session, product, errors=None, saved=False) -> dict:
    """Контекст полной карточки: сам товар + вычисляемые + справочники для форм."""
    return {
        "product": product,
        "view": product_view(session, product),
        "units": list(UnitEnum),
        "statuses": list(ProductStatus),
        "suppliers": list_active_suppliers(session),  # для datalist в форме правки
        "errors": errors or [],
        "saved": saved,
    }


@router.get("/products/new")
async def new_product_form(request: Request, session: Session = Depends(get_session)):
    created = request.query_params.get("created")
    return _render(request, "products/new.html", {
        "units": list(UnitEnum),
        "suppliers": list_active_suppliers(session),
        "errors": [],
        "values": {},
        "created_code": created,
    })


@router.get("/products/supplier-row")
async def supplier_row(request: Request):
    """Пустой ряд выбора поставщика — добавляется на форму по HTMX (hx-swap beforeend)."""
    return _render(request, "products/_supplier_row.html", {"value": ""})


@router.post("/products")
async def create_product_route(
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
    # Непустые имена поставщиков — для передачи в схему и перерисовки рядов при ошибке.
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

    article_clean = (article or "").strip() or None
    qr_code_clean = (qr_code or "").strip() or None
    min_stock_clean = (min_stock or "").strip() or "0"

    try:
        data = ProductCreate(
            name=name,
            price_buy=price_buy,
            price_sell=price_sell,
            quantity=quantity,
            unit=unit,
            article=article_clean,
            min_stock=min_stock_clean,
            qr_code=qr_code_clean,
            supplier_names=supplier_names,
        )
    except ValidationError as exc:
        errors = [err["msg"].removeprefix("Value error, ") for err in exc.errors()]
        return _render(request, "products/new.html", {
            "units": list(UnitEnum),
            "suppliers": list_active_suppliers(session),
            "errors": errors,
            "values": values,
            "created_code": None,
        }, status_code=422)

    try:
        product = create_product(data, session)
    except IntegrityError:
        session.rollback()
        return _render(request, "products/new.html", {
            "units": list(UnitEnum),
            "suppliers": list_active_suppliers(session),
            "errors": ["QR-код уже используется другим товаром"],
            "values": values,
            "created_code": None,
        }, status_code=422)

    return RedirectResponse(
        url=f"/products/new?created={product.numeric_code}",
        status_code=303,
    )


# ── Карточка товара (этап 3.1) ───────────────────────────────────────────────


@router.get("/products/search")
async def search_for_card(
    request: Request,
    q: str = "",
    session: Session = Depends(get_session),
):
    """Поиск товара для карточки (разд. 4). Тот же движок, что на кассе.

    К каждому товару прикладываем флаг low_stock (5.2, разд. 10.5) — считаем в роуте,
    чтобы не дублировать критерий минимума в шаблоне.
    """
    results = [(p, is_low_stock(p)) for p in search_products(session, q)]
    return _render(
        request,
        "products/_search_results.html",
        {"results": results, "query": q.strip()},
    )


@router.get("/products/{product_id}/card")
async def product_card(
    request: Request,
    product_id: int,
    session: Session = Depends(get_session),
):
    """Полная карточка товара: просмотр (5.2, 5.3) + инлайн-формы правки (5.5)."""
    product = get_product(session, product_id)
    if product is None:
        return HTMLResponse("Товар не найден", status_code=404)
    return _render(request, "products/_card.html", _card_context(session, product))


@router.post("/products/{product_id}")
async def update_product_route(
    request: Request,
    product_id: int,
    name: str = Form(...),
    price_buy: str = Form(...),
    price_sell: str = Form(...),
    unit: str = Form(...),
    status: str = Form(...),
    min_stock: Optional[str] = Form(None),
    extra_info: Optional[str] = Form(None),
    supplier: list[str] = Form(default=[]),
    session: Session = Depends(get_session),
):
    """Сохранить паспорт товара (5.5). Валидация до логики; ошибка → 422 + перерисовка."""
    product = get_product(session, product_id)
    if product is None:
        return HTMLResponse("Товар не найден", status_code=404)

    supplier_names = [s.strip() for s in supplier if s and s.strip()]
    try:
        data = ProductEdit(
            name=name,
            price_buy=price_buy,
            price_sell=price_sell,
            unit=unit,
            status=status,
            min_stock=(min_stock or "").strip() or "0",
            extra_info=extra_info,
            supplier_names=supplier_names,
        )
    except ValidationError as exc:
        errors = [err["msg"].removeprefix("Value error, ") for err in exc.errors()]
        return _render(
            request,
            "products/_card.html",
            _card_context(session, product, errors=errors),
            status_code=422,
        )

    updated = update_product(session, product_id, data)
    return _render(
        request, "products/_card.html", _card_context(session, updated, saved=True)
    )


@router.post("/products/{product_id}/quantity")
async def adjust_quantity_route(
    request: Request,
    product_id: int,
    quantity: str = Form(...),
    session: Session = Depends(get_session),
):
    """Корректировка остатка (5.5, склад): движение «инвентаризация» + новый остаток."""
    product = get_product(session, product_id)
    if product is None:
        return HTMLResponse("Товар не найден", status_code=404)

    try:
        data = QuantityAdjust(quantity=quantity)
    except ValidationError as exc:
        errors = [err["msg"].removeprefix("Value error, ") for err in exc.errors()]
        return _render(
            request,
            "products/_card.html",
            _card_context(session, product, errors=errors),
            status_code=422,
        )

    updated = adjust_quantity(session, product_id, data.quantity)
    return _render(
        request, "products/_card.html", _card_context(session, updated, saved=True)
    )


@router.get("/products/{product_id}/movements")
async def product_movements_section(
    request: Request,
    product_id: int,
    session: Session = Depends(get_session),
):
    """История движений (5.7) + статистика (5.4). Ленивая подгрузка секции карточки."""
    product = get_product(session, product_id)
    if product is None:
        return HTMLResponse("Товар не найден", status_code=404)
    return _render(
        request,
        "products/_movements.html",
        {
            "product": product,
            "movements": product_movements(session, product_id),
            "stats": product_stats(session, product_id),
        },
    )


@router.get("/products/{product_id}/prices")
async def product_prices_section(
    request: Request,
    product_id: int,
    session: Session = Depends(get_session),
):
    """Динамика цен (5.6): график двух ступенчатых кривых. Ленивая подгрузка секции."""
    product = get_product(session, product_id)
    if product is None:
        return HTMLResponse("Товар не найден", status_code=404)
    return _render(
        request,
        "products/_prices.html",
        {
            "product": product,
            "chart": build_price_chart(price_history(session, product_id)),
        },
    )
