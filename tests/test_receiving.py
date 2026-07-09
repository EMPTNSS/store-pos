"""Ручной приём накладной (этап 6.1, макет разд. 12.3).

Приоритет — складской и ценовой путь: приход прибавляет к остатку и пишет движение
«приход» (income), при изменении цены — одна точка price_history. Плюс граница
(ProductReceive), путь нового товара (переиспользование create_product) и HTTP-слой.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.models.movement import Movement, OperationType
from app.models.price_history import PriceHistory
from app.models.product import Product, UnitEnum
from app.schemas.product import ProductCreate, ProductReceive
from app.services.product_service import create_product, receive_product


def _make(session: Session, **overrides) -> Product:
    defaults = dict(
        name="Тест товар",
        price_buy="50.00",
        price_sell="100.00",
        quantity="5",
        unit=UnitEnum.piece,
    )
    defaults.update(overrides)
    return create_product(ProductCreate(**defaults), session)


def _income_movements(session: Session, product_id: int) -> list[Movement]:
    return session.exec(
        select(Movement).where(
            Movement.product_id == product_id,
            Movement.operation_type == OperationType.income,
        )
    ).all()


def _price_points(session: Session, product_id: int) -> list[PriceHistory]:
    return session.exec(
        select(PriceHistory).where(PriceHistory.product_id == product_id)
    ).all()


# ── Сервис receive_product (складской/ценовой путь) ──────────────────────────


class TestReceiveService:
    def test_income_raises_stock_and_writes_movement(self, db):
        product = _make(db)
        # После создания: 1 приход-движение (из create_product), остаток 5.
        assert len(_income_movements(db, product.id)) == 1

        updated = receive_product(db, product.id, Decimal("3"))

        assert updated.quantity_current == Decimal("8")  # 5 + 3
        incomes = _income_movements(db, product.id)
        assert len(incomes) == 2  # создание + этот приём
        last = max(incomes, key=lambda m: m.id)
        assert last.quantity == Decimal("3")  # знак +, ровно пришедшее

    def test_receive_without_price_change_no_history(self, db):
        product = _make(db)
        points_before = len(_price_points(db, product.id))  # 1 (из создания)

        updated = receive_product(db, product.id, Decimal("2"))

        assert updated.quantity_current == Decimal("7")
        assert updated.price_buy == 5000 and updated.price_sell == 10000
        assert len(_price_points(db, product.id)) == points_before  # не пополнена

    def test_receive_changes_buy_price_one_point(self, db):
        product = _make(db)
        before = len(_price_points(db, product.id))

        updated = receive_product(db, product.id, Decimal("1"), price_buy=6000)

        assert updated.price_buy == 6000
        assert updated.price_sell == 10000  # не тронута
        points = _price_points(db, product.id)
        assert len(points) == before + 1
        newest = max(points, key=lambda p: p.id)
        assert newest.price_buy == 6000 and newest.price_sell == 10000

    def test_receive_changes_both_prices_single_point(self, db):
        product = _make(db)
        before = len(_price_points(db, product.id))

        updated = receive_product(
            db, product.id, Decimal("1"), price_buy=5500, price_sell=11000
        )

        assert updated.price_buy == 5500 and updated.price_sell == 11000
        # Смена обеих цен даёт одну точку, не две.
        assert len(_price_points(db, product.id)) == before + 1

    def test_receive_weighted_decimal_quantity(self, db):
        product = _make(db, unit=UnitEnum.kg, quantity="1.5")

        updated = receive_product(db, product.id, Decimal("2.250"))

        assert updated.quantity_current == Decimal("3.750")

    def test_atomic_stock_movement_and_price_together(self, db):
        product = _make(db)
        movements_before = len(_income_movements(db, product.id))
        points_before = len(_price_points(db, product.id))

        receive_product(db, product.id, Decimal("4"), price_sell=12000)

        # Остаток, движение и точка истории появились вместе.
        fresh = db.get(Product, product.id)
        assert fresh.quantity_current == Decimal("9")
        assert len(_income_movements(db, product.id)) == movements_before + 1
        assert len(_price_points(db, product.id)) == points_before + 1


# ── Схема границы ProductReceive ─────────────────────────────────────────────


class TestReceiveSchema:
    def test_quantity_must_be_positive(self):
        with pytest.raises(ValidationError):
            ProductReceive(received_quantity="0")
        with pytest.raises(ValidationError):
            ProductReceive(received_quantity="-2")

    def test_negative_buy_price_rejected(self):
        with pytest.raises(ValidationError):
            ProductReceive(received_quantity="1", price_buy="-1")

    def test_non_positive_sell_price_rejected(self):
        with pytest.raises(ValidationError):
            ProductReceive(received_quantity="1", price_sell="0")

    def test_none_prices_pass_and_parse(self):
        # Пустые/None цены допустимы (не меняем); заданные — разбираются в копейки.
        data = ProductReceive(received_quantity="3", price_buy=None, price_sell="")
        assert data.price_buy is None and data.price_sell is None
        data2 = ProductReceive(received_quantity="3", price_buy="45.50")
        assert data2.price_buy == 4550


# ── Новый товар: переиспользование create_product (0.3) ──────────────────────


class TestCreateViaReceiving:
    def test_create_route_makes_product_with_income_and_price(self, client):
        resp = client.post(
            "/receiving/create",
            data={
                "name": "Новый через приём",
                "price_buy": "30.00",
                "price_sell": "55.00",
                "quantity": "7",
                "unit": UnitEnum.piece.value,
            },
        )
        assert resp.status_code == 200
        assert "Товар создан" in resp.text

    def test_create_route_invalid_returns_422(self, client):
        # Невалидная цена продажи доходит до ProductCreate → мой 422-фрагмент (не встроенный
        # FastAPI-422): Form(str) пропускает "0", схема отклоняет.
        resp = client.post(
            "/receiving/create",
            data={
                "name": "Плохая цена",
                "price_buy": "30.00",
                "price_sell": "0",  # продажа должна быть > 0
                "quantity": "7",
                "unit": UnitEnum.piece.value,
            },
        )
        assert resp.status_code == 422
        assert "больше 0" in resp.text


# ── HTTP-слой (TestClient) ───────────────────────────────────────────────────


class TestReceivingHttp:
    def _create(self, client, **data) -> str:
        defaults = dict(
            name="Молоко",
            price_buy="40.00",
            price_sell="60.00",
            quantity="10",
            unit=UnitEnum.piece.value,
        )
        defaults.update(data)
        client.post("/products", data=defaults)
        # Числовой код первого созданного товара — 000001.
        return "1"

    def test_search_hit_shows_receive_button(self, client):
        self._create(client)
        resp = client.get("/receiving/search", params={"q": "Молоко"})
        assert resp.status_code == 200
        assert "Оприходовать" in resp.text

    def test_search_miss_shows_create_button(self, client):
        resp = client.get("/receiving/search", params={"q": "Отсутствует"})
        assert resp.status_code == 200
        assert "Создать карточку" in resp.text

    def test_receive_form_and_404(self, client):
        self._create(client)
        ok = client.get("/receiving/1/form")
        assert ok.status_code == 200
        assert "Пришедшее количество" in ok.text
        assert client.get("/receiving/99999/form").status_code == 404

    def test_receive_valid_raises_stock(self, client):
        self._create(client)
        resp = client.post("/receiving/1", data={"received_quantity": "5"})
        assert resp.status_code == 200
        assert "Оприходовано" in resp.text
        # Остаток в БД поднят с 10 до 15 и записано движение «приход».
        from app.database import engine

        with Session(engine) as s:
            product = s.get(Product, 1)
            assert product.quantity_current == Decimal("15")
            assert len(_income_movements(s, 1)) == 2  # создание + приём

    def test_receive_invalid_quantity_422(self, client):
        self._create(client)
        resp = client.post("/receiving/1", data={"received_quantity": "0"})
        assert resp.status_code == 422
        assert "больше 0" in resp.text

    def test_panel_add_is_real(self, client):
        resp = client.get("/panels/add")
        assert resp.status_code == 200
        assert "в разработке" not in resp.text
        assert "add-test-field" not in resp.text
        assert "Поиск товара для приёма" in resp.text
