"""Полная карточка товара (этап 3.1, макет разд. 4, 5).

Приоритет — складской и ценовой путь: корректировка остатка (движение «инвентаризация»,
атомарность) и запись price_history при правке цены. Плюс просмотр/правка паспорта и HTTP-слой.
"""

from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.models.movement import Movement, OperationType
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus, UnitEnum
from app.schemas.product import ProductCreate, ProductEdit, QuantityAdjust
from app.services.product_service import (
    adjust_quantity,
    create_product,
    get_product,
    product_suppliers,
    product_view,
    update_product,
)


def _data(**overrides) -> ProductCreate:
    defaults = dict(
        name="Тест товар",
        price_buy="50.00",
        price_sell="100.00",
        quantity="5",
        unit=UnitEnum.piece,
    )
    defaults.update(overrides)
    return ProductCreate(**defaults)


def _edit(product: Product, **overrides) -> ProductEdit:
    """ProductEdit из текущих значений товара с точечными переопределениями.

    Цены передаём как int-копейки — Kopecks пропускает int без изменения, что и нужно
    для проверки «цена не менялась».
    """
    defaults = dict(
        name=product.name,
        price_buy=product.price_buy,
        price_sell=product.price_sell,
        unit=product.unit,
        min_stock=product.min_stock,
        status=product.status,
        supplier_names=[],
        extra_info=product.extra_info,
    )
    defaults.update(overrides)
    return ProductEdit(**defaults)


def _inventory_movements(session: Session, product_id: int) -> list[Movement]:
    return session.exec(
        select(Movement).where(
            Movement.product_id == product_id,
            Movement.operation_type == OperationType.inventory,
        )
    ).all()


def _price_points(session: Session, product_id: int) -> list[PriceHistory]:
    return session.exec(
        select(PriceHistory).where(PriceHistory.product_id == product_id)
    ).all()


# ── Корректировка количества (складской путь) ────────────────────────────────

def test_adjust_surplus_writes_positive_movement(db: Session):
    p = create_product(_data(quantity="5"), db)
    adjust_quantity(db, p.id, Decimal("8"))

    assert get_product(db, p.id).quantity_current == Decimal("8")
    invs = _inventory_movements(db, p.id)
    assert len(invs) == 1
    assert invs[0].quantity == Decimal("3")  # излишек +3


def test_adjust_shortage_writes_negative_movement(db: Session):
    p = create_product(_data(quantity="5"), db)
    adjust_quantity(db, p.id, Decimal("2"))

    assert get_product(db, p.id).quantity_current == Decimal("2")
    invs = _inventory_movements(db, p.id)
    assert len(invs) == 1
    assert invs[0].quantity == Decimal("-3")  # недостача −3


def test_adjust_no_change_writes_nothing(db: Session):
    p = create_product(_data(quantity="5"), db)
    adjust_quantity(db, p.id, Decimal("5"))

    assert get_product(db, p.id).quantity_current == Decimal("5")
    assert _inventory_movements(db, p.id) == []  # движение только на реальное изменение


def test_adjust_to_zero_allowed(db: Session):
    p = create_product(_data(quantity="4"), db)
    adjust_quantity(db, p.id, Decimal("0"))

    assert get_product(db, p.id).quantity_current == Decimal("0")
    assert len(_inventory_movements(db, p.id)) == 1


def test_quantity_adjust_rejects_negative():
    with pytest.raises(Exception):
        QuantityAdjust(quantity="-1")


# ── Правка паспорта: цены и price_history ────────────────────────────────────

def test_update_price_appends_history(db: Session):
    p = create_product(_data(price_buy="50.00", price_sell="100.00"), db)
    assert len(_price_points(db, p.id)) == 1  # точка от создания

    update_product(db, p.id, _edit(get_product(db, p.id), price_sell="120.00"))

    points = _price_points(db, p.id)
    assert len(points) == 2
    assert points[-1].price_sell == 12000
    assert points[-1].price_buy == 5000


def test_update_without_price_change_no_history(db: Session):
    p = create_product(_data(), db)
    update_product(db, p.id, _edit(get_product(db, p.id), name="Переименован"))

    assert len(_price_points(db, p.id)) == 1  # новых точек нет
    assert get_product(db, p.id).name == "Переименован"


def test_update_replaces_suppliers(db: Session):
    p = create_product(_data(supplier_names=["Альфа"]), db)
    update_product(db, p.id, _edit(get_product(db, p.id), supplier_names=["Бета", "Гамма"]))

    names = sorted(s.name for s in product_suppliers(db, p.id))
    assert names == ["Бета", "Гамма"]  # Альфа отвязана


def test_update_can_archive(db: Session):
    p = create_product(_data(), db)
    update_product(db, p.id, _edit(get_product(db, p.id), status=ProductStatus.archived))

    p2 = get_product(db, p.id)
    assert p2.status == ProductStatus.archived
    assert p2 is not None  # физически не удалён


def test_update_keeps_codes_and_stock(db: Session):
    p = create_product(_data(article="AB-1", quantity="7"), db)
    code, art, created, qty = (
        p.numeric_code, p.article, p.created_at, p.quantity_current,
    )
    update_product(
        db, p.id, _edit(get_product(db, p.id), name="Новое имя", price_sell="999.00")
    )

    p2 = get_product(db, p.id)
    assert p2.numeric_code == code
    assert p2.article == art
    assert p2.created_at == created
    assert p2.quantity_current == qty  # правка паспорта не трогает остаток
    assert p2.name == "Новое имя"


# ── Вычисляемые значения ─────────────────────────────────────────────────────

def test_product_view_margin(db: Session):
    p = create_product(_data(price_buy="40.00", price_sell="60.00", quantity="3"), db)
    v = product_view(db, p)

    assert v["margin_abs"] == 2000  # 20.00 ₽ в копейках
    assert v["margin_pct"] == Decimal("50.0")
    assert v["in_stock"] is True


def test_product_view_zero_buy_no_percent(db: Session):
    p = create_product(_data(price_buy="0", price_sell="10.00"), db)
    assert product_view(db, p)["margin_pct"] is None  # без деления на ноль


def test_product_view_in_stock_false(db: Session):
    p = create_product(_data(quantity="1"), db)
    adjust_quantity(db, p.id, Decimal("0"))
    assert product_view(db, get_product(db, p.id))["in_stock"] is False


# ── HTTP-слой ────────────────────────────────────────────────────────────────

def _create_via(engine, **overrides) -> int:
    with Session(engine) as s:
        return create_product(_data(**overrides), s).id


def test_http_card_ok(client, test_engine):
    pid = _create_via(test_engine, name="Молоко 2.5%")
    resp = client.get(f"/products/{pid}/card")
    assert resp.status_code == 200
    assert "Молоко 2.5%" in resp.text
    assert "Редактировать" in resp.text


def test_http_card_not_found(client):
    assert client.get("/products/999999/card").status_code == 404


def test_http_search_has_open_card(client, test_engine):
    _create_via(test_engine, name="Кефир")
    resp = client.get("/products/search", params={"q": "Кефир"})
    assert resp.status_code == 200
    assert "Открыть карточку" in resp.text
    assert 'data-product-id' in resp.text


def test_http_update_valid(client, test_engine):
    pid = _create_via(test_engine)
    resp = client.post(f"/products/{pid}", data={
        "name": "Обновлён",
        "price_buy": "50.00",
        "price_sell": "150.00",
        "unit": "шт",
        "status": "активный",
        "min_stock": "2",
        "extra_info": "хрупкое",
    })
    assert resp.status_code == 200
    assert "Обновлён" in resp.text
    with Session(test_engine) as s:
        p = s.get(Product, pid)
        assert p.price_sell == 15000
        assert p.extra_info == "хрупкое"


def test_http_update_invalid_empty_name(client, test_engine):
    pid = _create_via(test_engine)
    resp = client.post(f"/products/{pid}", data={
        "name": "   ",
        "price_buy": "50.00",
        "price_sell": "150.00",
        "unit": "шт",
        "status": "активный",
    })
    assert resp.status_code == 422


def test_http_update_invalid_price(client, test_engine):
    pid = _create_via(test_engine)
    resp = client.post(f"/products/{pid}", data={
        "name": "Товар",
        "price_buy": "50.00",
        "price_sell": "0",
        "unit": "шт",
        "status": "активный",
    })
    assert resp.status_code == 422


def test_http_quantity_valid_writes_movement(client, test_engine):
    pid = _create_via(test_engine, quantity="5")
    resp = client.post(f"/products/{pid}/quantity", data={"quantity": "9"})
    assert resp.status_code == 200
    with Session(test_engine) as s:
        assert s.get(Product, pid).quantity_current == Decimal("9")
        assert len(_inventory_movements(s, pid)) == 1


def test_http_quantity_negative_rejected(client, test_engine):
    pid = _create_via(test_engine)
    resp = client.post(f"/products/{pid}/quantity", data={"quantity": "-3"})
    assert resp.status_code == 422


def test_http_panel_products_is_search(client):
    resp = client.get("/panels/products")
    assert resp.status_code == 200
    assert "Поиск товара для карточки" in resp.text
    assert "в разработке" not in resp.text
