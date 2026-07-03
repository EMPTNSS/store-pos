from datetime import datetime, timedelta

import pytest

from app.models.receipt import PaymentMethod, Receipt
from app.schemas.product import ProductCreate
from app.services.cart import CartView, get_cart
from app.services.customer_display import (
    LastSale,
    get_last_sale,
    mark_sale_completed,
    reset_last_sale,
    resolve_display_state,
)
from app.services.product_service import create_product

THANKS = 8
NOW = datetime(2026, 7, 3, 14, 0, 0)


def _empty_cart() -> CartView:
    return CartView(lines=[], subtotal=0, rounding=0, grand_total=0)


def _filled_cart() -> CartView:
    # Резолверу важна только истинность cart.lines; содержимое строк не разбирается.
    return CartView(lines=[object()], subtotal=2000, rounding=0, grand_total=2000)


@pytest.fixture(autouse=True)
def _reset_marker():
    """Маркер продажи — модуль-синглтон; чистим до и после каждого теста."""
    reset_last_sale()
    yield
    reset_last_sale()


# --- резолвер состояния (чистая функция) ----------------------------------

class TestResolveDisplayState:
    def test_sale_when_cart_not_empty(self):
        cart = _filled_cart()
        state = resolve_display_state(cart, None, NOW, THANKS)
        assert state.kind == "sale"
        assert state.cart is cart

    def test_thanks_right_after_sale(self):
        sale = LastSale(number=1, total=2000, at=NOW)
        state = resolve_display_state(_empty_cart(), sale, NOW, THANKS)
        assert state.kind == "thanks"
        assert state.sale is sale

    def test_thanks_just_before_timeout(self):
        sale = LastSale(1, 2000, NOW - timedelta(seconds=THANKS - 1))
        state = resolve_display_state(_empty_cart(), sale, NOW, THANKS)
        assert state.kind == "thanks"

    def test_idle_exactly_at_timeout(self):
        sale = LastSale(1, 2000, NOW - timedelta(seconds=THANKS))
        state = resolve_display_state(_empty_cart(), sale, NOW, THANKS)
        assert state.kind == "idle"

    def test_idle_after_timeout(self):
        sale = LastSale(1, 2000, NOW - timedelta(seconds=THANKS + 5))
        state = resolve_display_state(_empty_cart(), sale, NOW, THANKS)
        assert state.kind == "idle"

    def test_idle_without_marker(self):
        state = resolve_display_state(_empty_cart(), None, NOW, THANKS)
        assert state.kind == "idle"

    def test_sale_priority_over_fresh_marker(self):
        sale = LastSale(1, 2000, NOW)
        state = resolve_display_state(_filled_cart(), sale, NOW, THANKS)
        assert state.kind == "sale"


# --- маркер последней продажи ---------------------------------------------

class TestMarker:
    def test_mark_and_get(self):
        receipt = Receipt(
            receipt_number=7,
            datetime=NOW,
            payment_method=PaymentMethod.cash,
            subtotal=2000,
            rounding=0,
            total=2000,
        )
        mark_sale_completed(receipt)
        last = get_last_sale()
        assert last is not None
        assert last.number == 7
        assert last.total == 2000
        assert last.at is not None

    def test_reset_clears_marker(self):
        mark_sale_completed(
            Receipt(receipt_number=1, datetime=NOW, payment_method=PaymentMethod.cash,
                    subtotal=0, rounding=0, total=0)
        )
        reset_last_sale()
        assert get_last_sale() is None


# --- HTTP-слой -------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cart():
    get_cart().clear()
    yield
    get_cart().clear()


@pytest.fixture(autouse=True)
def _isolate_print(monkeypatch):
    """Продажа в HTTP-тестах не пишет на реальные принтеры."""
    from app.config import settings

    monkeypatch.setattr(settings, "receipt_printer_backend", "null")
    monkeypatch.setattr(settings, "invoice_printer_backend", "null")
    yield


def _make_product(db, **overrides):
    data = dict(name="Хлеб", price_buy="10.00", price_sell="20.00", quantity="50", unit="шт")
    data.update(overrides)
    return create_product(ProductCreate(**data), db)


class TestCustomerRoutes:
    def test_customer_page_ok(self, db, client):
        resp = client.get("/customer")
        assert resp.status_code == 200
        assert 'hx-get="/customer/state"' in resp.text
        assert "every 1000ms" in resp.text  # интервал опроса из конфига (по умолчанию 1000)

    def test_state_shows_cart_lines(self, db, client):
        product = _make_product(db, name="Молоко", price_sell="15.50", quantity="50")
        client.post("/cashier/items", data={"numeric_code": product.numeric_code})

        resp = client.get("/customer/state")
        assert resp.status_code == 200
        assert "Молоко" in resp.text
        assert "15.50" in resp.text   # цена
        assert "Итого" in resp.text   # крупный итог

    def test_state_thanks_after_sale(self, db, client):
        product = _make_product(db, quantity="50")
        client.post("/cashier/items", data={"numeric_code": product.numeric_code})
        client.post("/cashier/complete", data={"payment_method": "cash"})

        resp = client.get("/customer/state")
        assert resp.status_code == 200
        assert "Спасибо за покупку" in resp.text  # макет 20.4

    def test_state_idle_without_sale(self, db, client):
        resp = client.get("/customer/state")
        assert resp.status_code == 200
        assert "Добро пожаловать" in resp.text
