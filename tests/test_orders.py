"""Заявки на пополнение (этап 5.3, макет разд. 11).

Детект кандидатов (минимум/закончился), ручной проброс с выбором поставщика, наполнение и
правка строк, закрытие и хранение закрытых. Ключевой инвариант: заявка НЕ трогает склад —
не пишет Movement и не меняет quantity_current.
"""

from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models.movement import Movement
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product, UnitEnum
from app.schemas.order import OrderLineInput
from app.schemas.product import ProductCreate
from app.services.order_service import (
    add_manual_line,
    close_order,
    is_order_candidate,
    list_closed_orders,
    list_open_orders,
    push_to_order,
    remove_line,
    update_line,
)
from app.services.product_service import create_product, product_suppliers
from app.services.supplier_service import resolve_suppliers


def _prod(qty: str, minimum: str, unit: UnitEnum = UnitEnum.piece) -> Product:
    """Непривязанный к БД товар для проверки чистого критерия."""
    return Product(
        name="x", numeric_code="000001", price_sell=100, price_buy=50,
        unit=unit, min_stock=Decimal(minimum), quantity_current=Decimal(qty),
    )


def _data(**overrides) -> ProductCreate:
    defaults = dict(
        name="Тест товар", price_buy="50.00", price_sell="100.00",
        quantity="5", unit=UnitEnum.piece,
    )
    defaults.update(overrides)
    return ProductCreate(**defaults)


def _make(session: Session, **overrides) -> Product:
    return create_product(_data(**overrides), session)


def _supplier_id(session: Session, product: Product) -> int:
    return product_suppliers(session, product.id)[0].id


# ── 1–4. Детектор кандидатов (макет 11.2) ────────────────────────────────────

def test_below_min_is_candidate():
    assert is_order_candidate(_prod(qty="2", minimum="5")) is True


def test_out_of_stock_without_min_is_candidate():
    # Закончился (qty<=0) кандидат даже при min_stock == 0 (5.2 отдал этот случай 5.3).
    assert is_order_candidate(_prod(qty="0", minimum="0")) is True


def test_normal_is_not_candidate():
    assert is_order_candidate(_prod(qty="8", minimum="5")) is False


def test_weight_product_decimal():
    assert is_order_candidate(_prod(qty="1.5", minimum="2.0", unit=UnitEnum.kg)) is True
    assert is_order_candidate(_prod(qty="3.0", minimum="2.0", unit=UnitEnum.kg)) is False


# ── 5. Проброс создаёт открытую заявку; склад не тронут ──────────────────────

def test_push_creates_open_order_no_stock_change(db: Session):
    product = _make(db, name="Молоко", quantity="1", min_stock="5",
                    supplier_names=["Ферма"])
    sid = _supplier_id(db, product)
    movements_before = len(db.exec(select(Movement)).all())
    qty_before = product.quantity_current

    order = push_to_order(db, product.id, sid, Decimal("10"), comment="срочно")

    assert order.status == OrderStatus.open
    assert order.store == settings.store_name
    lines = db.exec(select(OrderLine).where(OrderLine.order_id == order.id)).all()
    assert len(lines) == 1
    assert lines[0].needed_quantity == Decimal("10")
    assert lines[0].comment == "срочно"
    # Склад не изменён: ни остаток, ни новые движения.
    assert db.get(Product, product.id).quantity_current == qty_before
    assert len(db.exec(select(Movement)).all()) == movements_before


# ── 6. Второй товар тому же поставщику → та же заявка ─────────────────────────

def test_second_product_same_supplier_same_order(db: Session):
    p1 = _make(db, name="Молоко", quantity="1", min_stock="5", supplier_names=["Ферма"])
    sid = _supplier_id(db, p1)
    p2 = _make(db, name="Кефир", quantity="1", min_stock="3", supplier_names=["Ферма"])

    o1 = push_to_order(db, p1.id, sid, Decimal("4"))
    o2 = push_to_order(db, p2.id, sid, Decimal("6"))

    assert o1.id == o2.id  # одна открытая заявка на поставщика
    lines = db.exec(select(OrderLine).where(OrderLine.order_id == o1.id)).all()
    assert len(lines) == 2


# ── 7. Повторный проброс того же товара → строка обновлена, не дубль ──────────

def test_repeat_push_updates_line(db: Session):
    product = _make(db, name="Молоко", quantity="1", min_stock="5", supplier_names=["Ферма"])
    sid = _supplier_id(db, product)

    order = push_to_order(db, product.id, sid, Decimal("4"))
    push_to_order(db, product.id, sid, Decimal("9"), comment="обновили")

    lines = db.exec(select(OrderLine).where(OrderLine.order_id == order.id)).all()
    assert len(lines) == 1
    assert lines[0].needed_quantity == Decimal("9")
    assert lines[0].comment == "обновили"


# ── 8. Проброс без поставщика с новым именем → инлайн-создание ────────────────

def test_push_with_inline_new_supplier(db: Session):
    product = _make(db, name="Хлеб", quantity="1", min_stock="2")  # без поставщика
    assert product_suppliers(db, product.id) == []

    supplier = resolve_suppliers(["Пекарня №1"], db)[0]
    order = push_to_order(db, product.id, supplier.id, Decimal("3"))

    assert order.supplier_id == supplier.id
    assert supplier.name == "Пекарня №1"
    lines = db.exec(select(OrderLine).where(OrderLine.order_id == order.id)).all()
    assert len(lines) == 1


# ── 9. Ручное редактирование строк ────────────────────────────────────────────

def test_manual_edit_lines(db: Session):
    product = _make(db, name="Молоко", quantity="1", min_stock="5", supplier_names=["Ферма"])
    sid = _supplier_id(db, product)
    other = _make(db, name="Сахар", quantity="20", min_stock="0")  # не кандидат
    order = push_to_order(db, product.id, sid, Decimal("4"))

    # Изменить количество и комментарий.
    line = db.exec(select(OrderLine).where(OrderLine.order_id == order.id)).one()
    update_line(db, line.id, Decimal("7"), "поправили")
    db.refresh(line)
    assert line.needed_quantity == Decimal("7")
    assert line.comment == "поправили"

    # Добавить произвольный товар (не кандидата).
    add_manual_line(db, order.id, other.id, Decimal("2"))
    assert len(db.exec(select(OrderLine).where(OrderLine.order_id == order.id)).all()) == 2

    # Удалить строку.
    remove_line(db, line.id)
    remaining = db.exec(select(OrderLine).where(OrderLine.order_id == order.id)).all()
    assert len(remaining) == 1
    assert remaining[0].product_id == other.id


# ── 10. Закрытие: статус, дата, склад цел, правки закрытой отклонены ──────────

def test_close_order_and_reject_edits(db: Session):
    product = _make(db, name="Молоко", quantity="1", min_stock="5", supplier_names=["Ферма"])
    sid = _supplier_id(db, product)
    order = push_to_order(db, product.id, sid, Decimal("4"))
    line = db.exec(select(OrderLine).where(OrderLine.order_id == order.id)).one()
    movements_before = len(db.exec(select(Movement)).all())

    closed = close_order(db, order.id)
    assert closed.status == OrderStatus.closed
    assert closed.closed_at is not None
    # Склад не тронут; заявка осталась в БД.
    assert db.get(Product, product.id).quantity_current == Decimal("1")
    assert len(db.exec(select(Movement)).all()) == movements_before
    assert db.get(Order, order.id) is not None

    # Повторное закрытие и правки закрытой заявки — отклонены.
    with pytest.raises(ValueError):
        close_order(db, order.id)
    with pytest.raises(ValueError):
        update_line(db, line.id, Decimal("2"), None)
    with pytest.raises(ValueError):
        remove_line(db, line.id)
    with pytest.raises(ValueError):
        add_manual_line(db, order.id, product.id, Decimal("1"))


# ── 11. Граница: нужное количество > 0 ───────────────────────────────────────

def test_needed_quantity_must_be_positive():
    with pytest.raises(Exception):
        OrderLineInput(needed_quantity="0")
    with pytest.raises(Exception):
        OrderLineInput(needed_quantity="-3")
    assert OrderLineInput(needed_quantity="2.5").needed_quantity == Decimal("2.5")


# ── 12. HTTP: панель отрисовывает кандидатов, открытые, архив ─────────────────

def test_http_panel_renders(client, test_engine):
    with Session(test_engine) as s:
        _make(s, name="Сметана", quantity="1", min_stock="4", supplier_names=["Ферма"])
    resp = client.get("/orders/panel")
    assert resp.status_code == 200
    assert "Кандидаты на заказ" in resp.text
    assert "Сметана" in resp.text
    assert "в заявку" in resp.text  # кнопка «+»
    assert "Открытые заявки" in resp.text
    assert "Архив закрытых" in resp.text


# ── 13. HTTP: проброс с одним поставщиком → строка в заявке ───────────────────

def test_http_push_single_supplier(client, test_engine):
    with Session(test_engine) as s:
        product = _make(s, name="Молоко", quantity="1", min_stock="5",
                        supplier_names=["Ферма"])
        pid = product.id
    resp = client.post("/orders/push", data={
        "product_id": pid, "needed_quantity": "8",
    })
    assert resp.status_code == 200
    assert "Молоко" in resp.text
    with Session(test_engine) as s:
        line = s.exec(select(OrderLine)).one()
        assert line.needed_quantity == Decimal("8")


# ── 14. HTTP: несколько поставщиков → форма выбора; с выбором → добавлено ──────

def test_http_push_multiple_suppliers(client, test_engine):
    with Session(test_engine) as s:
        product = _make(s, name="Сок", quantity="1", min_stock="3",
                        supplier_names=["Ферма", "Оптовик"])
        pid = product.id
        sid = product_suppliers(s, pid)[1].id

    # Без выбора поставщика → вернулась форма выбора, строк ещё нет.
    resp = client.post("/orders/push", data={"product_id": pid, "needed_quantity": "5"})
    assert resp.status_code == 200
    assert "Выберите поставщика" in resp.text
    with Session(test_engine) as s:
        assert s.exec(select(OrderLine)).all() == []

    # С выбором поставщика → строка добавлена.
    resp = client.post("/orders/push", data={
        "product_id": pid, "supplier_id": sid, "needed_quantity": "5",
    })
    assert resp.status_code == 200
    with Session(test_engine) as s:
        line = s.exec(select(OrderLine)).one()
        assert line.needed_quantity == Decimal("5")


# ── 15. HTTP: закрытие → в архив, остаток цел ────────────────────────────────

def test_http_close_moves_to_archive(client, test_engine):
    with Session(test_engine) as s:
        product = _make(s, name="Молоко", quantity="1", min_stock="5",
                        supplier_names=["Ферма"])
        pid = product.id
    client.post("/orders/push", data={"product_id": pid, "needed_quantity": "8"})
    with Session(test_engine) as s:
        order_id = s.exec(select(Order)).one().id

    resp = client.post(f"/orders/{order_id}/close")
    assert resp.status_code == 200
    with Session(test_engine) as s:
        order = s.get(Order, order_id)
        assert order.status == OrderStatus.closed
        assert s.get(Product, pid).quantity_current == Decimal("1")
        assert len(list_closed_orders(s)) == 1
        assert len(list_open_orders(s)) == 0
