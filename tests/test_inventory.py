"""Инвентаризация (этап 5.1, макет разд. 13).

По решению заказчика инвентаризация = ручная правка количества на фактическое в карточке
товара, с отметкой в движении. Механизм построен на этапе 3.1 (`adjust_quantity`); этот
файл закрепляет сценарий разд. 13 под своим этапом и защищает складской путь инвентаризации
от регресса. Проверяем через публичные точки: сервис `adjust_quantity`, схему `QuantityAdjust`
и роут `POST /products/{id}/quantity`.
"""

from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.models.movement import Movement, OperationType
from app.models.product import Product, UnitEnum
from app.schemas.product import ProductCreate, QuantityAdjust
from app.services.product_service import (
    adjust_quantity,
    create_product,
    get_product,
    product_movements,
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


def _create_via(engine, **overrides) -> int:
    with Session(engine) as s:
        return create_product(_data(**overrides), s).id


def _inventory_movements(session: Session, product_id: int) -> list[Movement]:
    return session.exec(
        select(Movement).where(
            Movement.product_id == product_id,
            Movement.operation_type == OperationType.inventory,
        )
    ).all()


# ── Сервисный слой: adjust_quantity (складской путь, разд. 13.3.4–13.3.5) ──────

def test_shortage_writes_negative_movement(db: Session):
    """Факт < учёт → недостача: движение «инвентаризация» с отрицательной дельтой."""
    p = create_product(_data(quantity="5"), db)
    adjust_quantity(db, p.id, Decimal("2"))

    assert get_product(db, p.id).quantity_current == Decimal("2")
    invs = _inventory_movements(db, p.id)
    assert len(invs) == 1
    assert invs[0].quantity == Decimal("-3")


def test_surplus_writes_positive_movement(db: Session):
    """Факт > учёт → излишек: движение «инвентаризация» с положительной дельтой."""
    p = create_product(_data(quantity="5"), db)
    adjust_quantity(db, p.id, Decimal("8"))

    assert get_product(db, p.id).quantity_current == Decimal("8")
    invs = _inventory_movements(db, p.id)
    assert len(invs) == 1
    assert invs[0].quantity == Decimal("3")


def test_match_writes_nothing(db: Session):
    """Факт == учёт → расхождения нет: движение не пишется, остаток цел."""
    p = create_product(_data(quantity="5"), db)
    adjust_quantity(db, p.id, Decimal("5"))

    assert get_product(db, p.id).quantity_current == Decimal("5")
    assert _inventory_movements(db, p.id) == []


def test_zero_fact_is_full_shortage(db: Session):
    """Явный факт 0 при ненулевом учёте → недостача на весь остаток (не «не считали»)."""
    p = create_product(_data(quantity="4"), db)
    adjust_quantity(db, p.id, Decimal("0"))

    assert get_product(db, p.id).quantity_current == Decimal("0")
    invs = _inventory_movements(db, p.id)
    assert len(invs) == 1
    assert invs[0].quantity == Decimal("-4")


def test_fractional_weight_fact(db: Session):
    """Весовой товар: дробный факт → дробные дельта и остаток."""
    p = create_product(_data(unit=UnitEnum.kg, quantity="2"), db)
    adjust_quantity(db, p.id, Decimal("1.5"))

    assert get_product(db, p.id).quantity_current == Decimal("1.5")
    invs = _inventory_movements(db, p.id)
    assert len(invs) == 1
    assert invs[0].quantity == Decimal("-0.5")


# ── Граница: QuantityAdjust (разд. 3 ТЗ 3.1, отрицательный остаток запрещён) ──

def test_negative_fact_rejected():
    with pytest.raises(Exception):
        QuantityAdjust(quantity="-1")


# ── История движений (разд. 13.4–13.5): дата, количество, пометка ────────────

def test_movement_visible_in_history(db: Session):
    """После корректировки отметка «инвентаризация» видна в истории движений товара."""
    p = create_product(_data(quantity="5"), db)
    adjust_quantity(db, p.id, Decimal("7"))

    invs = [
        m
        for m in product_movements(db, p.id)
        if m.operation_type == OperationType.inventory
    ]
    assert len(invs) == 1
    m = invs[0]
    assert m.operation_type == OperationType.inventory  # пометка операции
    assert m.quantity == Decimal("2")                   # количество (дельта, излишек +)
    assert m.datetime is not None                       # дата операции


# ── HTTP-слой: POST /products/{id}/quantity ──────────────────────────────────

def test_http_fact_valid_writes_movement(client, test_engine):
    pid = _create_via(test_engine, quantity="5")
    resp = client.post(f"/products/{pid}/quantity", data={"quantity": "9"})
    assert resp.status_code == 200
    with Session(test_engine) as s:
        assert s.get(Product, pid).quantity_current == Decimal("9")
        assert len(_inventory_movements(s, pid)) == 1


def test_http_fact_negative_rejected(client, test_engine):
    pid = _create_via(test_engine, quantity="5")
    resp = client.post(f"/products/{pid}/quantity", data={"quantity": "-3"})
    assert resp.status_code == 422
    with Session(test_engine) as s:
        # Остаток не тронут, движения инвентаризации нет.
        assert s.get(Product, pid).quantity_current == Decimal("5")
        assert _inventory_movements(s, pid) == []
