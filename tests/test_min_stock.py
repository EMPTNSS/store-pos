"""Минимальный остаток (этап 5.2, макет разд. 10).

Обнаружение достижения минимума (`is_low_stock`, порог включителен, ноль = «не задан»),
флаг во `product_view` и визуальные пометки в карточке и в поиске раздела «Товары».
Касса (денежный путь) визуально не трогается.
"""

from decimal import Decimal

from sqlmodel import Session

from app.models.product import Product, UnitEnum
from app.services.product_service import (
    create_product,
    get_product,
    is_low_stock,
    product_view,
)
from app.schemas.product import ProductCreate


def _prod(qty: str, minimum: str, unit: UnitEnum = UnitEnum.piece) -> Product:
    """Непривязанный к БД товар для проверки чистого критерия."""
    return Product(
        name="x",
        numeric_code="000001",
        price_sell=100,
        price_buy=50,
        unit=unit,
        min_stock=Decimal(minimum),
        quantity_current=Decimal(qty),
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


# ── Критерий обнаружения (складская логика, разд. 4 ТЗ) ───────────────────────

def test_below_min_is_low():
    assert is_low_stock(_prod(qty="2", minimum="5")) is True


def test_equal_to_min_is_low():
    # Порог включителен: остаток, равный минимуму, уже «достигнут».
    assert is_low_stock(_prod(qty="5", minimum="5")) is True


def test_above_min_is_not_low():
    assert is_low_stock(_prod(qty="8", minimum="5")) is False


def test_min_zero_never_low():
    # min_stock == 0 = «минимум не задан» → отслеживание выключено даже при нулевом остатке.
    assert is_low_stock(_prod(qty="0", minimum="0")) is False
    assert is_low_stock(_prod(qty="5", minimum="0")) is False


def test_weight_product_decimal_threshold():
    assert is_low_stock(_prod(qty="1.5", minimum="2.0", unit=UnitEnum.kg)) is True
    assert is_low_stock(_prod(qty="2.5", minimum="2.0", unit=UnitEnum.kg)) is False


def test_zero_stock_with_min_is_low():
    # Закончившийся товар — частный случай «ниже минимума», когда порог задан.
    assert is_low_stock(_prod(qty="0", minimum="3")) is True


# ── Флаг во view ──────────────────────────────────────────────────────────────

def test_product_view_low_stock_matches_helper(db: Session):
    low = create_product(_data(quantity="2", min_stock="5"), db)
    ok = create_product(_data(quantity="10", min_stock="5", name="Другой"), db)

    assert product_view(db, low)["low_stock"] is True
    assert product_view(db, ok)["low_stock"] is False
    assert product_view(db, low)["low_stock"] == is_low_stock(low)


# ── HTTP: карточка ────────────────────────────────────────────────────────────

def _create_via(engine, **overrides) -> int:
    with Session(engine) as s:
        return create_product(_data(**overrides), s).id


def test_http_card_low_shows_notice(client, test_engine):
    pid = _create_via(test_engine, name="Молоко", quantity="2", min_stock="5")
    resp = client.get(f"/products/{pid}/card")
    assert resp.status_code == 200
    assert "достигнут минимальный остаток" in resp.text


def test_http_card_normal_no_notice(client, test_engine):
    pid = _create_via(test_engine, name="Кефир", quantity="10", min_stock="5")
    resp = client.get(f"/products/{pid}/card")
    assert resp.status_code == 200
    assert "достигнут минимальный остаток" not in resp.text


def test_http_card_min_not_set_shown(client, test_engine):
    pid = _create_via(test_engine, name="Хлеб", quantity="4", min_stock="0")
    resp = client.get(f"/products/{pid}/card")
    assert "не задан" in resp.text
    assert "достигнут минимальный остаток" not in resp.text


# ── HTTP: поиск раздела «Товары» ──────────────────────────────────────────────

def test_http_search_low_shows_badge(client, test_engine):
    _create_via(test_engine, name="Сметана", quantity="1", min_stock="4")
    resp = client.get("/products/search", params={"q": "Сметана"})
    assert resp.status_code == 200
    assert "ниже минимума" in resp.text


def test_http_search_normal_no_badge(client, test_engine):
    _create_via(test_engine, name="Творог", quantity="20", min_stock="4")
    resp = client.get("/products/search", params={"q": "Творог"})
    assert "ниже минимума" not in resp.text


# ── HTTP: переоценка на лету при правке минимума ──────────────────────────────

def test_http_raise_min_makes_low(client, test_engine):
    # Остаток 5, минимум 2 → не низкий; поднимаем минимум до 10 → карточка помечает.
    pid = _create_via(test_engine, name="Кофе", quantity="5", min_stock="2")
    resp = client.post(f"/products/{pid}", data={
        "name": "Кофе",
        "price_buy": "50.00",
        "price_sell": "150.00",
        "unit": "шт",
        "status": "активный",
        "min_stock": "10",
    })
    assert resp.status_code == 200
    assert "достигнут минимальный остаток" in resp.text
    with Session(test_engine) as s:
        assert get_product(s, pid).min_stock == Decimal("10")


# ── Регрессия: касса не помечает низкий остаток ───────────────────────────────

def test_cashier_search_has_no_badge(client, test_engine):
    _create_via(test_engine, name="Сахар", quantity="1", min_stock="9")
    resp = client.get("/cashier/search", params={"q": "Сахар"})
    assert resp.status_code == 200
    assert "ниже минимума" not in resp.text  # денежный путь визуально чист
