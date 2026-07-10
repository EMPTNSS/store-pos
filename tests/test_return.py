from decimal import Decimal

import pytest
from sqlmodel import select

from app.models.movement import Movement, OperationType
from app.models.product import UnitEnum
from app.models.receipt import PaymentMethod, Receipt
from app.models.return_receipt import ReturnReceipt, ReturnReceiptLine
from app.schemas.product import ProductCreate
from app.services.product_service import create_product
from app.services.return_cart import ReturnCart, get_return_cart
from app.services.return_service import complete_return
from app.services.sale import complete_sale
from app.services.cart import Cart
from app.services.work_day_service import close_day, get_open_day, open_day


@pytest.fixture(autouse=True)
def _reset_return_cart():
    """Корзина возврата — модуль-синглтон; чистим её до и после каждого теста."""
    get_return_cart().clear()
    yield
    get_return_cart().clear()


@pytest.fixture(autouse=True)
def _open_work_day(db):
    """Возврат оформляется только в открытую смену (guard 7.1-prep): открываем день на тест."""
    open_day(db)
    yield


def _make_product(db, **overrides):
    data = dict(
        name="Хлеб", price_buy="10.00", price_sell="20.00",
        quantity="50", unit="шт",
    )
    data.update(overrides)
    return create_product(ProductCreate(**data), db)


# --- сервис complete_return -----------------------------------------------

class TestCompleteReturn:
    def test_return_receipt_saved_with_total(self, db):
        p1 = _make_product(db, name="Хлеб", price_sell="20.00")   # 2000
        p2 = _make_product(db, name="Молоко", price_sell="15.50")  # 1550
        cart = ReturnCart()
        cart.add(p1, Decimal("2"))  # 40.00
        cart.add(p2, Decimal("3"))  # 46.50
        view = cart.view()

        receipt = complete_return(db, cart, PaymentMethod.cash)

        assert receipt.return_number is not None
        assert receipt.datetime is not None
        assert receipt.payment_method == PaymentMethod.cash
        assert receipt.total == view.total == 8650  # без округления вверх до ₽

    def test_return_bound_to_open_day(self, db):
        p = _make_product(db, price_sell="20.00")
        cart = ReturnCart()
        cart.add(p, Decimal("1"))
        receipt = complete_return(db, cart, PaymentMethod.cash)

        day = get_open_day(db)
        assert day is not None
        assert receipt.work_day_id == day.id

    def test_return_rejected_without_open_day(self, db):
        close_day(db)  # смена, открытая autouse-фикстурой, закрыта
        p = _make_product(db, price_sell="20.00")
        cart = ReturnCart()
        cart.add(p, Decimal("2"))

        with pytest.raises(ValueError):
            complete_return(db, cart, PaymentMethod.cash)

        # Возврат не создан, черновик цел (guard сработал до любых мутаций).
        assert db.exec(select(ReturnReceipt)).all() == []
        assert len(cart.view().lines) == 1

    def test_total_invariant_no_round_up(self, db):
        # Итог с копейками сохраняется как есть, а не поднимается до рубля.
        p = _make_product(db, price_sell="15.50")
        cart = ReturnCart()
        cart.add(p, Decimal("3"))  # 46.50
        receipt = complete_return(db, cart, PaymentMethod.card)

        lines = db.exec(
            select(ReturnReceiptLine).where(
                ReturnReceiptLine.return_receipt_id == receipt.id
            )
        ).all()
        assert receipt.total == sum(line.total for line in lines) == 4650

    def test_return_lines_snapshot(self, db):
        p = _make_product(db, name="Молоко", price_sell="15.50", unit="л")
        cart = ReturnCart()
        cart.add(p, Decimal("2"))
        expected_total = cart.view().lines[0].total

        receipt = complete_return(db, cart, PaymentMethod.cash)

        lines = db.exec(
            select(ReturnReceiptLine).where(
                ReturnReceiptLine.return_receipt_id == receipt.id
            )
        ).all()
        assert len(lines) == 1
        line = lines[0]
        assert line.name == "Молоко"
        assert line.unit == UnitEnum.liter
        assert line.price == 1550
        assert line.quantity == Decimal("2")
        assert line.total == expected_total == 3100

    def test_stock_incremented(self, db):
        p = _make_product(db, quantity="50")
        cart = ReturnCart()
        cart.add(p, Decimal("3"))
        complete_return(db, cart, PaymentMethod.cash)
        db.refresh(p)
        assert p.quantity_current == Decimal("53")

    def test_stock_incremented_fractional(self, db):
        p = _make_product(db, quantity="10", unit="кг")
        cart = ReturnCart()
        cart.add(p, Decimal("1.5"))
        complete_return(db, cart, PaymentMethod.cash)
        db.refresh(p)
        assert p.quantity_current == Decimal("11.5")

    def test_movement_written_positive(self, db):
        p = _make_product(db, quantity="50")
        cart = ReturnCart()
        cart.add(p, Decimal("3"))
        receipt = complete_return(db, cart, PaymentMethod.cash)

        returns = db.exec(
            select(Movement).where(
                Movement.product_id == p.id,
                Movement.operation_type == OperationType.return_,
            )
        ).all()
        assert len(returns) == 1
        assert returns[0].quantity == Decimal("3")
        assert returns[0].datetime == receipt.datetime

    def test_return_numbers_sequential_and_independent(self, db):
        p = _make_product(db, quantity="50")

        # Продажа занимает receipt_number, но не двигает return_number.
        sale_cart = Cart()
        sale_cart.add(p, Decimal("1"))
        complete_sale(db, sale_cart, PaymentMethod.cash)

        c1 = ReturnCart()
        c1.add(p, Decimal("1"))
        r1 = complete_return(db, c1, PaymentMethod.cash)

        c2 = ReturnCart()
        c2.add(p, Decimal("1"))
        r2 = complete_return(db, c2, PaymentMethod.cash)

        assert r1.return_number == 1  # своя последовательность, не зависит от продаж
        assert r2.return_number == r1.return_number + 1

    def test_atomic_rollback_on_error(self, db, monkeypatch):
        p = _make_product(db, quantity="50")
        cart = ReturnCart()
        cart.add(p, Decimal("3"))

        def _boom():
            raise RuntimeError("сбой commit")

        monkeypatch.setattr(db, "commit", _boom)
        with pytest.raises(RuntimeError):
            complete_return(db, cart, PaymentMethod.cash)
        db.rollback()

        assert db.exec(select(ReturnReceipt)).all() == []
        assert db.exec(select(ReturnReceiptLine)).all() == []
        returns = db.exec(
            select(Movement).where(Movement.operation_type == OperationType.return_)
        ).all()
        assert returns == []
        db.refresh(p)
        assert p.quantity_current == Decimal("50")

    def test_empty_return_rejected(self, db):
        cart = ReturnCart()
        with pytest.raises(ValueError):
            complete_return(db, cart, PaymentMethod.cash)
        assert db.exec(select(ReturnReceipt)).all() == []

    def test_custom_price_snapshot(self, db):
        p = _make_product(db, price_sell="20.00")
        cart = ReturnCart()
        line = cart.add(p, Decimal("2"))
        cart.set_price(line.line_id, 1000)  # продавец правит: 10.00 ₽ за единицу
        receipt = complete_return(db, cart, PaymentMethod.cash)

        # карточка дорожает после возврата — сохранённый чек не меняется
        p.price_sell = 9999
        db.add(p)
        db.commit()

        saved = db.exec(
            select(ReturnReceiptLine).where(
                ReturnReceiptLine.return_receipt_id == receipt.id
            )
        ).one()
        assert saved.price == 1000
        assert saved.total == 2000

    def test_cart_cleared_after_return(self, db):
        p = _make_product(db)
        cart = ReturnCart()
        cart.add(p, Decimal("1"))
        complete_return(db, cart, PaymentMethod.cash)
        assert cart.view().lines == []

    def test_cart_not_cleared_on_empty_return(self, db):
        cart = ReturnCart()
        with pytest.raises(ValueError):
            complete_return(db, cart, PaymentMethod.cash)
        assert cart.view().lines == []


# --- корзина возврата ReturnCart ------------------------------------------

class TestReturnCart:
    def test_add_twice_merges_and_snapshots_price(self, db):
        p = _make_product(db, price_sell="20.00")
        cart = ReturnCart()
        cart.add(p, Decimal("1"))
        cart.add(p, Decimal("2"))
        view = cart.view()
        assert len(view.lines) == 1
        assert view.lines[0].quantity == Decimal("3")
        assert view.lines[0].price == 2000  # снимок price_sell

    def test_set_price_recomputes_total(self, db):
        p = _make_product(db, price_sell="20.00")
        cart = ReturnCart()
        line = cart.add(p, Decimal("2"))
        cart.set_price(line.line_id, 1500)
        assert cart.view().lines[0].total == 3000

    def test_set_quantity_non_positive_rejected(self, db):
        p = _make_product(db)
        cart = ReturnCart()
        line = cart.add(p, Decimal("1"))
        with pytest.raises(ValueError):
            cart.set_quantity(line.line_id, Decimal("0"))


# --- HTTP-слой ------------------------------------------------------------

class TestReturnRoutes:
    def test_add_item(self, db, client):
        product = _make_product(db, quantity="50")
        resp = client.post("/returns/items", data={"numeric_code": product.numeric_code})
        assert resp.status_code == 200
        assert product.name in resp.text

    def test_complete_success(self, db, client):
        product = _make_product(db, quantity="50")
        client.post("/returns/items", data={"numeric_code": product.numeric_code})

        resp = client.post("/returns/complete", data={"payment_method": "cash"})
        assert resp.status_code == 200
        assert "Возврат оформлен" in resp.text
        assert "Возврат пуст" in resp.text  # корзина очищена

        receipts = db.exec(select(ReturnReceipt)).all()
        assert len(receipts) == 1
        assert receipts[0].payment_method == PaymentMethod.cash

        db.refresh(product)
        assert product.quantity_current == Decimal("51")

        returns = db.exec(
            select(Movement).where(Movement.operation_type == OperationType.return_)
        ).all()
        assert len(returns) == 1

    def test_complete_empty_return(self, db, client):
        resp = client.post("/returns/complete", data={"payment_method": "cash"})
        assert resp.status_code == 200
        assert "Возврат пуст" in resp.text
        assert db.exec(select(ReturnReceipt)).all() == []

    def test_complete_invalid_payment(self, db, client):
        product = _make_product(db, quantity="50")
        client.post("/returns/items", data={"numeric_code": product.numeric_code})

        resp = client.post("/returns/complete", data={"payment_method": "возврат"})
        assert resp.status_code == 200
        assert "Выберите способ возврата" in resp.text
        assert db.exec(select(ReturnReceipt)).all() == []
        assert product.name in resp.text  # корзина цела

    def test_clear(self, db, client):
        product = _make_product(db, quantity="50")
        client.post("/returns/items", data={"numeric_code": product.numeric_code})

        resp = client.post("/returns/clear")
        assert resp.status_code == 200
        assert "Возврат пуст" in resp.text
        assert get_return_cart().view().lines == []
