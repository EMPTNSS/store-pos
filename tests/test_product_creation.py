import datetime as _dt
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.models.counter import ProductCodeCounter
from app.models.movement import Movement, OperationType
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus, UnitEnum
from app.schemas.product import ProductCreate
from app.services.product_service import create_product


def _valid_data(**overrides) -> ProductCreate:
    defaults = dict(
        name="Тест товар",
        price_buy="50.00",
        price_sell="100.00",
        quantity="5",
        unit=UnitEnum.piece,
    )
    defaults.update(overrides)
    return ProductCreate(**defaults)


# ── 1. Валидный вызов создаёт product с верными полями ───────────────────────
def test_create_product_basic(db: Session):
    data = _valid_data()
    product = create_product(data, db)

    assert product.id is not None
    assert product.name == "Тест товар"
    assert product.price_buy == 5000
    assert product.price_sell == 10000
    assert product.unit == UnitEnum.piece
    assert isinstance(product.price_buy, int)
    assert isinstance(product.price_sell, int)


# ── 2. numeric_code уникальный, непустой; при двух подряд растёт ─────────────
def test_numeric_code_unique_and_grows(db: Session):
    p1 = create_product(_valid_data(name="Товар А"), db)
    p2 = create_product(_valid_data(name="Товар Б"), db)

    assert p1.numeric_code
    assert p2.numeric_code
    assert p1.numeric_code != p2.numeric_code
    assert int(p2.numeric_code) > int(p1.numeric_code)


# ── 3. status нового товара = активный ───────────────────────────────────────
def test_new_product_status_active(db: Session):
    product = create_product(_valid_data(), db)
    assert product.status == ProductStatus.active


# ── 4. Ровно одна movement типа income с верным quantity; price_buy нет ───────
def test_movement_income_only(db: Session):
    data = _valid_data(quantity="3.5")
    product = create_product(data, db)

    movements = db.exec(select(Movement).where(Movement.product_id == product.id)).all()
    assert len(movements) == 1

    m = movements[0]
    assert m.operation_type == OperationType.income
    assert m.quantity == Decimal("3.500")
    # Модель Movement не хранит цену — проверяем что поля price_buy нет
    assert not hasattr(m, "price_buy"), "Movement не должна хранить price_buy (п. 8.1 ТЗ)"


# ── 5. quantity_current равен введённому количеству ───────────────────────────
def test_quantity_current_equals_input(db: Session):
    data = _valid_data(quantity="7.25")
    product = create_product(data, db)
    db.refresh(product)
    assert product.quantity_current == Decimal("7.250")


# ── 6. price_history: одна запись, верные price_buy/price_sell, не-null datetime
def test_price_history_created(db: Session):
    before = _dt.datetime.now()
    data = _valid_data(price_buy="50.00", price_sell="99.99")
    product = create_product(data, db)

    rows = db.exec(
        select(PriceHistory).where(PriceHistory.product_id == product.id)
    ).all()
    assert len(rows) == 1, "Должна быть ровно одна запись в price_history"

    ph = rows[0]
    assert ph.price_buy == 5000, "50.00 ₽ = 5000 коп."
    assert ph.price_sell == 9999, "99.99 ₽ = 9999 коп."
    assert ph.datetime is not None
    assert isinstance(ph.datetime, _dt.datetime)
    assert ph.datetime >= before


# ── 7. quantity <= 0 → ValidationError, в БД ничего не создано ───────────────
def test_quantity_zero_or_negative_rejected(db: Session):
    with pytest.raises(ValidationError):
        _valid_data(quantity="0")

    with pytest.raises(ValidationError):
        _valid_data(quantity="-1")

    with pytest.raises(ValidationError):
        _valid_data(quantity="-0.001")

    assert db.exec(select(Product)).all() == []


# ── 8. Пустое name → ValidationError ─────────────────────────────────────────
def test_empty_name_rejected():
    with pytest.raises(ValidationError):
        _valid_data(name="")

    with pytest.raises(ValidationError):
        _valid_data(name="   ")


# ── 9. unit не из enum → ValidationError ─────────────────────────────────────
def test_invalid_unit_rejected():
    with pytest.raises(ValidationError):
        _valid_data(unit="галлон")

    with pytest.raises(ValidationError):
        _valid_data(unit="")


# ── 10. article/min_stock пропущены → товар создаётся, min_stock = 0 ──────────
def test_optional_fields_defaults(db: Session):
    data = ProductCreate(
        name="Без артикула",
        price_buy="10.00",
        price_sell="20.00",
        quantity="1",
        unit=UnitEnum.kg,
        # article и min_stock — не передаём, должны быть None и 0
    )
    product = create_product(data, db)
    db.refresh(product)

    assert product.article is None
    assert product.min_stock == Decimal("0")


# ── 11. Атомарность: сбой на шаге price_history → полный rollback ─────────────
def test_atomicity_on_failure(test_engine, monkeypatch):
    from app.database import init_db
    import app.services.product_service as svc

    init_db()  # создать таблицы и засеять счётчик

    class _FailingPriceHistory:
        def __init__(self, **kwargs):
            raise RuntimeError("simulated failure at price_history step")

    monkeypatch.setattr(svc, "PriceHistory", _FailingPriceHistory)

    data = _valid_data()

    with pytest.raises(RuntimeError, match="simulated failure"):
        with Session(test_engine) as s:
            create_product(data, s)

    # Новая сессия — проверяем что в БД ничего нет, счётчик не изменился
    with Session(test_engine) as s:
        assert s.exec(select(Product)).all() == [], "Product не должен быть в БД после rollback"
        assert s.exec(select(Movement)).all() == [], "Movement не должен быть в БД после rollback"
        counter = s.get(ProductCodeCounter, 1)
        assert counter is not None
        assert counter.last_value == 0, "Счётчик должен остаться на 0 после rollback"


# ── 12. Счётчик инкрементируется; with_for_update() присутствует в коде ───────
def test_counter_increments(db: Session):
    counter_initial = db.get(ProductCodeCounter, 1)
    assert counter_initial.last_value == 0

    create_product(_valid_data(name="Первый"), db)
    create_product(_valid_data(name="Второй"), db)

    counter_final = db.get(ProductCodeCounter, 1)
    assert counter_final.last_value == 2


def test_counter_with_for_update_in_source():
    import inspect
    import app.services.product_service as svc

    src = inspect.getsource(svc)
    assert "with_for_update" in src, "Сервис должен использовать with_for_update() для счётчика"


# ── Дубль qr_code: IntegrityError → форма с ошибкой, не 500 ─────────────────
def test_duplicate_qr_code_returns_form_error(client):
    payload = {
        "name": "Уникальный товар",
        "price_buy": "10.00",
        "price_sell": "20.00",
        "quantity": "5",
        "unit": "шт",
        "qr_code": "QR-DUPE-001",
    }

    # Первый товар — должен создаться без ошибок
    r1 = client.post("/products", data=payload, follow_redirects=False)
    assert r1.status_code == 303, f"Первый POST: ожидался 303, получен {r1.status_code}"

    # Второй товар с тем же qr_code — IntegrityError → форма с ошибкой
    r2 = client.post(
        "/products",
        data={**payload, "name": "Другой товар"},
        follow_redirects=False,
    )
    assert r2.status_code == 422, f"Дубль qr_code: ожидался 422, получен {r2.status_code}"
    assert "QR-код" in r2.text, "В ответе должно быть сообщение об ошибке QR-кода"
    # Убеждаемся, что вернулась форма, а не JSON с трассировкой
    assert "<form" in r2.text, "Ответ должен содержать HTML-форму, не 500"


# ── Smoke: HTTP POST /products создаёт товар и возвращает 303 ─────────────────
def test_http_create_product_smoke(client):
    resp = client.post(
        "/products",
        data={
            "name": "HTTP Товар",
            "price_buy": "30.00",
            "price_sell": "60.00",
            "quantity": "10",
            "unit": "шт",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"Ожидался 303, получен {resp.status_code}"
    location = resp.headers.get("location", "")
    assert "created=" in location, f"Ожидался 'created=' в location, получен: {location}"
