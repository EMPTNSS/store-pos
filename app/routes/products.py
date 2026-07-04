from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.database import get_session
from app.models.product import UnitEnum
from app.schemas.product import ProductCreate
from app.services.product_service import create_product
from app.services.supplier_service import list_active_suppliers

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


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
