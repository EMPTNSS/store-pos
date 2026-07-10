"""История движений и статистика товара (этап 3.2, макет 5.7/5.4).

Приоритет — денежный путь: чистая прибыль считается по закупочной цене на момент
каждой продажи (buy_price_asof через price_history), а не по текущей price_buy.
Количественная статистика (продано, приход/убытие по датам) — из movement.
"""

import datetime as _dt
from decimal import Decimal

import pytest
from sqlmodel import Session

from app.models.movement import OperationType
from app.models.price_history import PriceHistory
from app.models.product import Product, UnitEnum
from app.models.receipt import PaymentMethod, Receipt, ReceiptLine
from app.schemas.product import ProductCreate
from app.services.cart import Cart
from app.services.product_service import (
    adjust_quantity,
    buy_price_asof,
    create_product,
    get_product,
    product_movements,
    product_stats,
)
from app.services.sale import complete_sale
from app.services.work_day_service import open_day


@pytest.fixture(autouse=True)
def _open_work_day(db):
    """Продажа возможна только в открытую смену (guard 7.1-prep): открываем день на тест."""
    open_day(db)
    yield


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


def _sell(db: Session, product_id: int, qty: str) -> None:
    """Продать qty единиц товара одним чеком (пишет чек + движение «продажа»)."""
    cart = Cart()
    cart.add(get_product(db, product_id), Decimal(qty))
    complete_sale(db, cart, PaymentMethod.cash)


# ── Окно истории движений (5.7) ──────────────────────────────────────────────

def test_movements_desc_and_types(db: Session):
    p = create_product(_data(quantity="5"), db)  # приход +5
    _sell(db, p.id, "2")                          # продажа −2 (остаток 3)
    adjust_quantity(db, p.id, Decimal("6"))       # инвентаризация +3

    ms = product_movements(db, p.id)
    types = {m.operation_type for m in ms}
    assert OperationType.income in types
    assert OperationType.sale in types
    assert OperationType.inventory in types

    dts = [m.datetime for m in ms]
    assert dts == sorted(dts, reverse=True)  # от новых к старым


def test_movements_empty_for_bare_product(db: Session):
    # Товар, вставленный напрямую, минуя create_product → нет движений.
    p = Product(name="Пустой", numeric_code="990001", price_sell=1000,
                price_buy=500, unit=UnitEnum.piece)
    db.add(p)
    db.commit()
    assert product_movements(db, p.id) == []


# ── Количественная статистика (5.4) ──────────────────────────────────────────

def test_sold_total(db: Session):
    p = create_product(_data(quantity="10"), db)
    _sell(db, p.id, "3")
    assert product_stats(db, p.id)["sold_total"] == Decimal("3")


def test_by_date_income_outgoing(db: Session):
    p = create_product(_data(quantity="5"), db)  # приход +5 (сегодня)
    adjust_quantity(db, p.id, Decimal("8"))       # излишек +3 (сегодня)
    adjust_quantity(db, p.id, Decimal("6"))       # недостача −2 (сегодня)

    stats = product_stats(db, p.id)
    assert len(stats["by_date"]) == 1
    row = stats["by_date"][0]
    assert row["income"] == Decimal("8")    # 5 + 3
    assert row["outgoing"] == Decimal("2")


# ── Чистая прибыль: as-of по price_history (ключевое решение) ─────────────────

def test_net_profit_uses_asof_buy_price(db: Session):
    """Себестоимость первой продажи — по старой закупке, второй — по новой."""
    p = Product(name="Т", numeric_code="900001", price_sell=2000, price_buy=1500,
                unit=UnitEnum.piece)
    db.add(p)
    db.flush()
    # Закупка была 1000, затем поднялась до 1500.
    db.add(PriceHistory(product_id=p.id, datetime=_dt.datetime(2026, 1, 1),
                        price_buy=1000, price_sell=2000))
    db.add(PriceHistory(product_id=p.id, datetime=_dt.datetime(2026, 1, 2),
                        price_buy=1500, price_sell=2000))
    # Продажа 1: 2 шт между точками → закупка as-of = 1000.
    r1 = Receipt(receipt_number=1, datetime=_dt.datetime(2026, 1, 1, 12),
                 payment_method=PaymentMethod.cash, subtotal=4000, rounding=0, total=4000)
    db.add(r1)
    db.flush()
    db.add(ReceiptLine(receipt_id=r1.id, product_id=p.id, name="Т", unit=UnitEnum.piece,
                       price_sell=2000, quantity=Decimal("2"), total=4000))
    # Продажа 2: 3 шт после второй точки → закупка as-of = 1500.
    r2 = Receipt(receipt_number=2, datetime=_dt.datetime(2026, 1, 3, 12),
                 payment_method=PaymentMethod.cash, subtotal=6000, rounding=0, total=6000)
    db.add(r2)
    db.flush()
    db.add(ReceiptLine(receipt_id=r2.id, product_id=p.id, name="Т", unit=UnitEnum.piece,
                       price_sell=2000, quantity=Decimal("3"), total=6000))
    db.commit()

    # (4000 − 1000·2) + (6000 − 1500·3) = 2000 + 1500 = 3500
    assert product_stats(db, p.id)["net_profit"] == 3500


def test_net_profit_no_sales_zero(db: Session):
    p = create_product(_data(), db)
    assert product_stats(db, p.id)["net_profit"] == 0


def test_net_profit_zero_buy_equals_revenue(db: Session):
    p = create_product(_data(price_buy="0.00", price_sell="20.00", quantity="10"), db)
    _sell(db, p.id, "2")
    # Себестоимость 0 → прибыль = выручке = line_total(2000, 2) = 4000.
    assert product_stats(db, p.id)["net_profit"] == 4000


# ── buy_price_asof ───────────────────────────────────────────────────────────

def test_buy_price_asof_picks_last_before(db: Session):
    p = Product(name="Т", numeric_code="900002", price_sell=2000, price_buy=777,
                unit=UnitEnum.piece)
    db.add(p)
    db.flush()
    db.add(PriceHistory(product_id=p.id, datetime=_dt.datetime(2026, 1, 1),
                        price_buy=1000, price_sell=2000))
    db.add(PriceHistory(product_id=p.id, datetime=_dt.datetime(2026, 1, 5),
                        price_buy=1500, price_sell=2000))
    db.commit()

    assert buy_price_asof(db, p.id, _dt.datetime(2026, 1, 3)) == 1000
    assert buy_price_asof(db, p.id, _dt.datetime(2026, 1, 10)) == 1500
    # Раньше всех точек → нет точки ≤ at → fallback на текущую price_buy.
    assert buy_price_asof(db, p.id, _dt.datetime(2025, 12, 31)) == 777


def test_buy_price_asof_no_points_fallback(db: Session):
    p = Product(name="Т", numeric_code="900003", price_sell=2000, price_buy=555,
                unit=UnitEnum.piece)
    db.add(p)
    db.commit()
    assert buy_price_asof(db, p.id, _dt.datetime.now()) == 555


# ── HTTP-слой ────────────────────────────────────────────────────────────────

def _create_via(engine, **overrides) -> int:
    with Session(engine) as s:
        return create_product(_data(**overrides), s).id


def test_http_movements_ok(client, test_engine):
    pid = _create_via(test_engine, name="Молоко")
    resp = client.get(f"/products/{pid}/movements")
    assert resp.status_code == 200
    assert "Статистика" in resp.text
    assert "История движений" in resp.text
    assert "приход" in resp.text  # движение-приход от создания товара


def test_http_movements_not_found(client):
    assert client.get("/products/999999/movements").status_code == 404


def test_http_movements_empty_state(client, test_engine):
    with Session(test_engine) as s:
        p = Product(name="Пустой", numeric_code="990002", price_sell=1000,
                    price_buy=500, unit=UnitEnum.piece)
        s.add(p)
        s.commit()
        pid = p.id
    resp = client.get(f"/products/{pid}/movements")
    assert resp.status_code == 200
    assert "Движений по товару пока нет" in resp.text
