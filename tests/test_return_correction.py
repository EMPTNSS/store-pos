"""Исправление завершённого чека через возврат (этап 4.2, макет разд. 15.2–15.3).

Проверяем корректирующий возврат, привязанный к чеку-первоисточнику: снимок цены по чеку,
защиту от перевозврата (складской/денежный инвариант), правила «один возврат = один чек»
и регрессию свободного возврата 4.1. Критерии приёмки — разд. 11 ТЗ 4.2.
"""

from decimal import Decimal

import pytest
from sqlmodel import select

from app.models.movement import Movement, OperationType
from app.models.product import UnitEnum
from app.models.receipt import PaymentMethod, Receipt, ReceiptLine
from app.models.return_receipt import ReturnReceipt, ReturnReceiptLine
from app.schemas.product import ProductCreate
from app.services.cart import Cart
from app.services.product_service import create_product
from app.services.return_cart import ReturnCart, get_return_cart
from app.services.return_service import (
    already_returned,
    complete_return,
    returnable_lines,
)
from app.services.sale import complete_sale
from app.services.work_day_service import open_day


@pytest.fixture(autouse=True)
def _reset_return_cart():
    """Корзина возврата — модуль-синглтон; чистим её до и после каждого теста."""
    get_return_cart().clear()
    yield
    get_return_cart().clear()


@pytest.fixture(autouse=True)
def _open_work_day(db):
    """Продажа/возврат возможны только в открытую смену (guard 7.1-prep): открываем день."""
    open_day(db)
    yield


def _make_product(db, **overrides):
    data = dict(
        name="Хлеб", price_buy="10.00", price_sell="20.00",
        quantity="50", unit="шт",
    )
    data.update(overrides)
    return create_product(ProductCreate(**data), db)


def _sell(db, product, quantity=Decimal("1"), payment=PaymentMethod.cash):
    """Провести продажу и вернуть сохранённый чек продажи (первоисточник для 4.2)."""
    cart = Cart()
    cart.add(product, quantity)
    return complete_sale(db, cart, payment)


def _line_of(db, receipt, product):
    return db.exec(
        select(ReceiptLine).where(
            ReceiptLine.receipt_id == receipt.id,
            ReceiptLine.product_id == product.id,
        )
    ).one()


# --- Доступное к возврату / lookup ----------------------------------------

class TestReturnable:
    def test_already_returned_sum(self, db):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("5"))
        rl = _line_of(db, sale, p)

        assert already_returned(db, rl.id) == Decimal("0")  # непокрытая строка

        cart = ReturnCart()
        cart.add_from_receipt_line(rl, Decimal("2"))
        complete_return(db, cart, PaymentMethod.cash)
        cart2 = ReturnCart()
        cart2.add_from_receipt_line(rl, Decimal("1"))
        complete_return(db, cart2, PaymentMethod.cash)

        assert already_returned(db, rl.id) == Decimal("3")  # Σ по source_line_id

    def test_returnable_lines_available(self, db):
        p1 = _make_product(db, name="Хлеб", quantity="50")
        p2 = _make_product(db, name="Молоко", quantity="50", price_sell="15.50")
        cart = Cart()
        cart.add(p1, Decimal("4"))
        cart.add(p2, Decimal("2"))
        sale = complete_sale(db, cart, PaymentMethod.cash)

        rl1 = _line_of(db, sale, p1)
        # частично вернём одну строку
        rc = ReturnCart()
        rc.add_from_receipt_line(rl1, Decimal("1"))
        complete_return(db, rc, PaymentMethod.cash)

        rows = {r.line.product_id: r for r in returnable_lines(db, sale)}
        assert rows[p1.id].sold == Decimal("4")
        assert rows[p1.id].returned == Decimal("1")
        assert rows[p1.id].available == Decimal("3")
        assert rows[p2.id].available == Decimal("2")  # ничего не возвращено


# --- Корзина (корректирующие строки) --------------------------------------

class TestCorrectiveCart:
    def test_add_from_receipt_line_snapshot(self, db):
        p = _make_product(db, name="Молоко", price_sell="15.50", unit="л", quantity="50")
        sale = _sell(db, p, Decimal("3"))
        rl = _line_of(db, sale, p)

        cart = ReturnCart()
        line = cart.add_from_receipt_line(rl, Decimal("2"))

        assert line.price == 1550                 # цена из ReceiptLine.price_sell
        assert line.source_line_id == rl.id
        assert line.price_locked is True
        assert line.name == "Молоко"
        assert line.unit == UnitEnum.liter
        assert cart.source_receipt_id == sale.id

    def test_add_same_line_merges(self, db):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("5"))
        rl = _line_of(db, sale, p)

        cart = ReturnCart()
        cart.add_from_receipt_line(rl, Decimal("1"))
        cart.add_from_receipt_line(rl, Decimal("2"))
        view = cart.view()
        assert len(view.lines) == 1
        assert view.lines[0].quantity == Decimal("3")

    def test_locked_price_not_editable(self, db):
        p = _make_product(db, price_sell="20.00", quantity="50")
        sale = _sell(db, p, Decimal("2"))
        rl = _line_of(db, sale, p)

        cart = ReturnCart()
        line = cart.add_from_receipt_line(rl, Decimal("2"))
        with pytest.raises(ValueError):
            cart.set_price(line.line_id, 1000)

        # даже если карточка подорожает — цена в корзине из чека не меняется
        p.price_sell = 9999
        db.add(p)
        db.commit()
        assert cart.view().lines[0].price == 2000

    def test_mixing_free_into_corrective_rejected(self, db):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("3"))
        rl = _line_of(db, sale, p)

        cart = ReturnCart()
        cart.add_from_receipt_line(rl, Decimal("1"))
        with pytest.raises(ValueError):
            cart.add(p, Decimal("1"))  # свободный поверх корректирующего

    def test_mixing_corrective_over_free_rejected(self, db):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("3"))
        rl = _line_of(db, sale, p)

        cart = ReturnCart()
        cart.add(p, Decimal("1"))  # свободная строка
        with pytest.raises(ValueError):
            cart.add_from_receipt_line(rl, Decimal("1"))

    def test_mixing_two_receipts_rejected(self, db):
        p = _make_product(db, quantity="50")
        sale1 = _sell(db, p, Decimal("2"))
        sale2 = _sell(db, p, Decimal("2"))
        rl1 = _line_of(db, sale1, p)
        rl2 = _line_of(db, sale2, p)

        cart = ReturnCart()
        cart.add_from_receipt_line(rl1, Decimal("1"))
        with pytest.raises(ValueError):
            cart.add_from_receipt_line(rl2, Decimal("1"))


# --- Сервис complete_return (корректирующий) ------------------------------

class TestCompleteCorrective:
    def test_saved_with_source_links(self, db):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("5"))
        rl = _line_of(db, sale, p)

        cart = ReturnCart()
        cart.add_from_receipt_line(rl, Decimal("2"))
        rr = complete_return(db, cart, PaymentMethod.cash)

        assert rr.source_receipt_id == sale.id
        line = db.exec(
            select(ReturnReceiptLine).where(
                ReturnReceiptLine.return_receipt_id == rr.id
            )
        ).one()
        assert line.source_line_id == rl.id
        assert line.total == 4000  # 2 × 20.00
        assert rr.total == 4000

        db.refresh(p)
        assert p.quantity_current == Decimal("47")  # 50 − 5 (продажа) + 2 (возврат)

        movements = db.exec(
            select(Movement).where(
                Movement.product_id == p.id,
                Movement.operation_type == OperationType.return_,
            )
        ).all()
        assert len(movements) == 1
        assert movements[0].quantity == Decimal("2")

    def test_over_return_blocked(self, db):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("2"))
        rl = _line_of(db, sale, p)
        db.refresh(p)
        stock_after_sale = p.quantity_current  # 48

        cart = ReturnCart()
        # обходим верхнюю границу корзины напрямую: количество больше проданного
        line = cart.add_from_receipt_line(rl, Decimal("2"))
        cart.set_quantity(line.line_id, Decimal("3"))

        with pytest.raises(ValueError):
            complete_return(db, cart, PaymentMethod.cash)

        assert db.exec(select(ReturnReceipt)).all() == []
        assert db.exec(select(ReturnReceiptLine)).all() == []
        assert db.exec(
            select(Movement).where(Movement.operation_type == OperationType.return_)
        ).all() == []
        db.refresh(p)
        assert p.quantity_current == stock_after_sale  # остаток не тронут

    def test_partial_then_rest_then_over(self, db):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("3"))
        rl = _line_of(db, sale, p)

        c1 = ReturnCart()
        c1.add_from_receipt_line(rl, Decimal("1"))
        complete_return(db, c1, PaymentMethod.cash)

        c2 = ReturnCart()
        c2.add_from_receipt_line(rl, Decimal("2"))  # остаток строки
        complete_return(db, c2, PaymentMethod.cash)

        assert already_returned(db, rl.id) == Decimal("3")

        c3 = ReturnCart()
        c3.add_from_receipt_line(rl, Decimal("1"))  # сверх проданного
        with pytest.raises(ValueError):
            complete_return(db, c3, PaymentMethod.cash)

    def test_full_receipt_return_no_rounding(self, db):
        # Чек с надбавкой округления: subtotal с копейками → total вверх до ₽.
        p = _make_product(db, price_sell="15.50", quantity="50")  # 3 × 15.50 = 46.50
        sale = _sell(db, p, Decimal("3"))
        assert sale.rounding > 0  # 46.50 → 47.00
        assert sale.subtotal == 4650
        rl = _line_of(db, sale, p)

        cart = ReturnCart()
        cart.add_from_receipt_line(rl, Decimal("3"))
        rr = complete_return(db, cart, PaymentMethod.cash)

        # Возврат = Σ строк по ценам чека, без надбавки округления.
        assert rr.total == sale.subtotal == 4650
        assert rr.total != sale.total  # надбавка ≤ 1 ₽ не возвращается


# --- Регрессия 4.1 --------------------------------------------------------

class TestFreeReturnRegression:
    def test_free_return_still_works(self, db):
        p = _make_product(db, price_sell="20.00", quantity="50")
        cart = ReturnCart()
        line = cart.add(p, Decimal("2"))
        cart.set_price(line.line_id, 1000)  # свободная цена редактируется
        rr = complete_return(db, cart, PaymentMethod.cash)

        assert rr.source_receipt_id is None
        saved = db.exec(
            select(ReturnReceiptLine).where(
                ReturnReceiptLine.return_receipt_id == rr.id
            )
        ).one()
        assert saved.source_line_id is None
        assert saved.price == 1000
        assert rr.total == 2000  # без округления
        db.refresh(p)
        assert p.quantity_current == Decimal("52")


# --- HTTP-слой ------------------------------------------------------------

class TestCorrectiveRoutes:
    def test_receipt_lookup_found_and_missing(self, db, client):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("2"))

        resp = client.get(f"/returns/receipt?number={sale.receipt_number}")
        assert resp.status_code == 200
        assert p.name in resp.text
        assert "Доступно" in resp.text

        miss = client.get("/returns/receipt?number=99999")
        assert "не найден" in miss.text

    def test_from_receipt_adds_locked_line(self, db, client):
        p = _make_product(db, price_sell="20.00", quantity="50")
        sale = _sell(db, p, Decimal("3"))
        rl = _line_of(db, sale, p)

        resp = client.post(
            "/returns/from-receipt",
            data={"source_line_id": rl.id, "quantity": "2"},
        )
        assert resp.status_code == 200
        assert p.name in resp.text
        assert "🔒" in resp.text  # цена по чеку показана как текст с замком
        assert get_return_cart().view().lines[0].price_locked is True

    def test_from_receipt_over_available_rejected(self, db, client):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("2"))
        rl = _line_of(db, sale, p)

        resp = client.post(
            "/returns/from-receipt",
            data={"source_line_id": rl.id, "quantity": "5"},
        )
        assert resp.status_code == 200
        assert "Доступно" in resp.text
        assert get_return_cart().view().lines == []  # корзина цела

    def test_complete_corrective_via_http(self, db, client):
        p = _make_product(db, quantity="50")
        sale = _sell(db, p, Decimal("3"))
        rl = _line_of(db, sale, p)
        client.post(
            "/returns/from-receipt",
            data={"source_line_id": rl.id, "quantity": "2"},
        )

        resp = client.post("/returns/complete", data={"payment_method": "cash"})
        assert resp.status_code == 200
        assert "Возврат оформлен" in resp.text
        assert f"№{sale.receipt_number}" in resp.text  # ссылка на чек продажи

        rr = db.exec(
            select(ReturnReceipt).where(ReturnReceipt.source_receipt_id == sale.id)
        ).one()
        line = db.exec(
            select(ReturnReceiptLine).where(
                ReturnReceiptLine.return_receipt_id == rr.id
            )
        ).one()
        assert line.source_line_id == rl.id
        db.refresh(p)
        assert p.quantity_current == Decimal("49")  # 50 − 3 + 2
        assert db.exec(
            select(Movement).where(Movement.operation_type == OperationType.return_)
        ).all()

    def test_price_change_on_locked_rejected_http(self, db, client):
        p = _make_product(db, price_sell="20.00", quantity="50")
        sale = _sell(db, p, Decimal("2"))
        rl = _line_of(db, sale, p)
        client.post(
            "/returns/from-receipt",
            data={"source_line_id": rl.id, "quantity": "2"},
        )
        line_id = get_return_cart().view().lines[0].line_id

        resp = client.post(
            f"/returns/items/{line_id}/price", data={"price": "5.00"}
        )
        assert resp.status_code == 200
        assert "не редактируется" in resp.text
        assert get_return_cart().view().lines[0].price == 2000  # цена не изменена
