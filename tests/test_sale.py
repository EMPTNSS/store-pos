from decimal import Decimal

import pytest
from sqlmodel import select

from app.models.movement import Movement, OperationType
from app.models.product import UnitEnum
from app.models.receipt import PaymentMethod, Receipt, ReceiptLine
from app.schemas.product import ProductCreate
from app.services.cart import Cart, get_cart
from app.services.money import round_total_up
from app.services.product_service import create_product
from app.services.sale import complete_sale


@pytest.fixture(autouse=True)
def _reset_cart():
    """Корзина — модуль-синглтон; чистим её до и после каждого теста."""
    get_cart().clear()
    yield
    get_cart().clear()


def _make_product(db, **overrides):
    data = dict(
        name="Хлеб", price_buy="10.00", price_sell="20.00",
        quantity="50", unit="шт",
    )
    data.update(overrides)
    return create_product(ProductCreate(**data), db)


# --- сервис complete_sale -------------------------------------------------

class TestCompleteSale:
    def test_receipt_saved_with_fixed_totals(self, db):
        p1 = _make_product(db, name="Хлеб", price_sell="20.00")   # 2000
        p2 = _make_product(db, name="Молоко", price_sell="15.50")  # 1550
        cart = Cart()
        cart.add(p1, Decimal("2"))  # 40.00
        cart.add(p2, Decimal("3"))  # 46.50
        view = cart.view()

        receipt = complete_sale(db, cart, PaymentMethod.cash)

        assert receipt.receipt_number is not None
        assert receipt.datetime is not None
        assert receipt.payment_method == PaymentMethod.cash
        assert receipt.subtotal == view.subtotal == 8650
        assert receipt.rounding == view.rounding == 50
        assert receipt.total == view.grand_total == 8700

    def test_total_invariant(self, db):
        p = _make_product(db, price_sell="15.50")
        cart = Cart()
        cart.add(p, Decimal("3"))  # 46.50 → округление вверх до 47.00
        receipt = complete_sale(db, cart, PaymentMethod.card)
        assert receipt.total == receipt.subtotal + receipt.rounding
        assert receipt.total == round_total_up(receipt.subtotal)

    def test_receipt_lines_snapshot(self, db):
        p = _make_product(db, name="Молоко", price_sell="15.50", unit="л")
        cart = Cart()
        cart.add(p, Decimal("2"))
        expected_total = cart.view().lines[0].total

        receipt = complete_sale(db, cart, PaymentMethod.cash)

        lines = db.exec(
            select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id)
        ).all()
        assert len(lines) == 1
        line = lines[0]
        assert line.name == "Молоко"
        assert line.unit == UnitEnum.liter
        assert line.price_sell == 1550
        assert line.quantity == Decimal("2")
        assert line.total == expected_total == 3100

    def test_stock_decremented(self, db):
        p = _make_product(db, quantity="50")
        cart = Cart()
        cart.add(p, Decimal("3"))
        complete_sale(db, cart, PaymentMethod.cash)
        db.refresh(p)
        assert p.quantity_current == Decimal("47")

    def test_stock_decremented_fractional(self, db):
        p = _make_product(db, quantity="10", unit="кг")
        cart = Cart()
        cart.add(p, Decimal("1.5"))
        complete_sale(db, cart, PaymentMethod.cash)
        db.refresh(p)
        assert p.quantity_current == Decimal("8.5")

    def test_movement_written_negative(self, db):
        p = _make_product(db, quantity="50")
        cart = Cart()
        cart.add(p, Decimal("3"))
        receipt = complete_sale(db, cart, PaymentMethod.cash)

        sales = db.exec(
            select(Movement).where(
                Movement.product_id == p.id,
                Movement.operation_type == OperationType.sale,
            )
        ).all()
        assert len(sales) == 1
        assert sales[0].quantity == Decimal("-3")
        assert sales[0].datetime == receipt.datetime

    def test_receipt_numbers_sequential(self, db):
        p = _make_product(db, quantity="50")

        cart1 = Cart()
        cart1.add(p, Decimal("1"))
        r1 = complete_sale(db, cart1, PaymentMethod.cash)

        cart2 = Cart()
        cart2.add(p, Decimal("1"))
        r2 = complete_sale(db, cart2, PaymentMethod.cash)

        assert r2.receipt_number == r1.receipt_number + 1

    def test_atomic_rollback_on_error(self, db, monkeypatch):
        p = _make_product(db, quantity="50")
        cart = Cart()
        cart.add(p, Decimal("3"))

        def _boom():
            raise RuntimeError("сбой commit")

        monkeypatch.setattr(db, "commit", _boom)
        with pytest.raises(RuntimeError):
            complete_sale(db, cart, PaymentMethod.cash)
        db.rollback()

        # Ни чек, ни строки, ни движение-продажа не сохранены; остаток не тронут.
        assert db.exec(select(Receipt)).all() == []
        assert db.exec(select(ReceiptLine)).all() == []
        sales = db.exec(
            select(Movement).where(Movement.operation_type == OperationType.sale)
        ).all()
        assert sales == []
        db.refresh(p)
        assert p.quantity_current == Decimal("50")

    def test_empty_cart_rejected(self, db):
        cart = Cart()
        with pytest.raises(ValueError):
            complete_sale(db, cart, PaymentMethod.cash)
        assert db.exec(select(Receipt)).all() == []

    def test_price_snapshot_after_sale(self, db):
        p = _make_product(db, price_sell="20.00")
        cart = Cart()
        cart.add(p, Decimal("2"))
        receipt = complete_sale(db, cart, PaymentMethod.cash)

        # карточка дорожает после продажи — сохранённый чек не меняется
        p.price_sell = 9999
        db.add(p)
        db.commit()

        line = db.exec(
            select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id)
        ).one()
        assert line.price_sell == 2000
        assert line.total == 4000

    def test_cart_cleared_after_sale(self, db):
        p = _make_product(db)
        cart = Cart()
        cart.add(p, Decimal("1"))
        complete_sale(db, cart, PaymentMethod.cash)
        assert cart.view().lines == []

    def test_cart_not_cleared_on_empty_sale(self, db):
        cart = Cart()
        with pytest.raises(ValueError):
            complete_sale(db, cart, PaymentMethod.cash)
        assert cart.view().lines == []  # и так пуст, но продажа не «съела» состояние

    def test_payment_method_recorded(self, db):
        p = _make_product(db, quantity="50")
        cart_cash = Cart()
        cart_cash.add(p, Decimal("1"))
        r_cash = complete_sale(db, cart_cash, PaymentMethod.cash)

        cart_card = Cart()
        cart_card.add(p, Decimal("1"))
        r_card = complete_sale(db, cart_card, PaymentMethod.card)

        assert r_cash.payment_method == PaymentMethod.cash
        assert r_card.payment_method == PaymentMethod.card


# --- HTTP-слой ------------------------------------------------------------

class TestCompleteRoute:
    def test_complete_success(self, db, client):
        product = _make_product(db, quantity="50")
        client.post("/cashier/items", data={"numeric_code": product.numeric_code})

        resp = client.post("/cashier/complete", data={"payment_method": "cash"})
        assert resp.status_code == 200
        assert "Продажа завершена" in resp.text
        assert "Чек пуст" in resp.text  # корзина очищена

        receipts = db.exec(select(Receipt)).all()
        assert len(receipts) == 1
        assert receipts[0].payment_method == PaymentMethod.cash

        db.refresh(product)
        assert product.quantity_current == Decimal("49")

    def test_complete_empty_cart(self, db, client):
        resp = client.post("/cashier/complete", data={"payment_method": "cash"})
        assert resp.status_code == 200
        assert "Чек пуст" in resp.text
        assert db.exec(select(Receipt)).all() == []

    def test_complete_invalid_payment(self, db, client):
        product = _make_product(db, quantity="50")
        client.post("/cashier/items", data={"numeric_code": product.numeric_code})

        resp = client.post("/cashier/complete", data={"payment_method": "чек"})
        assert resp.status_code == 200
        assert "Выберите способ оплаты" in resp.text
        assert db.exec(select(Receipt)).all() == []
        # корзина цела: строка на месте
        assert product.name in resp.text
