from decimal import Decimal

import pytest

from app.models.product import Product, UnitEnum
from app.schemas.product import ProductCreate
from app.services.cart import Cart, get_cart
from app.services.product_service import create_product


@pytest.fixture(autouse=True)
def _reset_cart():
    """Корзина — модуль-синглтон; чистим её до и после каждого теста."""
    get_cart().clear()
    yield
    get_cart().clear()


def _product(id=1, name="Хлеб", unit=UnitEnum.piece, price_sell=2000):
    return Product(
        id=id, name=name, numeric_code=f"{id:06d}",
        price_sell=price_sell, price_buy=1000, unit=unit,
    )


# --- сервис корзины -------------------------------------------------------

class TestCartService:
    def test_add_new_line(self):
        cart = Cart()
        cart.add(_product())
        view = cart.view()
        assert len(view.lines) == 1
        assert view.lines[0].quantity == Decimal("1")
        assert view.grand_total == 2000

    def test_add_same_product_merges(self):
        cart = Cart()
        cart.add(_product(id=1))
        cart.add(_product(id=1))
        view = cart.view()
        assert len(view.lines) == 1
        assert view.lines[0].quantity == Decimal("2")
        assert view.grand_total == 4000

    def test_add_different_products_separate_lines(self):
        cart = Cart()
        cart.add(_product(id=1, name="Хлеб"))
        cart.add(_product(id=2, name="Молоко"))
        assert len(cart.view().lines) == 2

    def test_set_quantity(self):
        cart = Cart()
        line = cart.add(_product())
        cart.set_quantity(line.line_id, Decimal("5"))
        assert cart.view().lines[0].quantity == Decimal("5")
        assert cart.view().grand_total == 10000

    def test_set_quantity_fractional(self):
        cart = Cart()
        line = cart.add(_product(unit=UnitEnum.kg, price_sell=3333))
        cart.set_quantity(line.line_id, Decimal("1.5"))
        # 1.5 × 33.33 = 49.995 → 50.00
        assert cart.view().grand_total == 5000

    def test_set_quantity_zero_rejected(self):
        cart = Cart()
        line = cart.add(_product())
        with pytest.raises(ValueError):
            cart.set_quantity(line.line_id, Decimal("0"))

    def test_set_quantity_negative_rejected(self):
        cart = Cart()
        line = cart.add(_product())
        with pytest.raises(ValueError):
            cart.set_quantity(line.line_id, Decimal("-1"))

    def test_remove(self):
        cart = Cart()
        line = cart.add(_product())
        cart.remove(line.line_id)
        assert cart.view().lines == []
        assert cart.view().grand_total == 0

    def test_clear(self):
        cart = Cart()
        cart.add(_product(id=1))
        cart.add(_product(id=2))
        cart.clear()
        assert cart.view().lines == []

    def test_price_snapshot_unchanged(self):
        cart = Cart()
        product = _product(price_sell=2000)
        cart.add(product)
        # карточка товара дорожает после добавления в чек
        product.price_sell = 9999
        assert cart.view().lines[0].price_sell == 2000
        assert cart.view().grand_total == 2000

    def test_subtotal_is_sum_of_lines(self):
        cart = Cart()
        cart.add(_product(id=1, price_sell=2000), Decimal("2"))  # 40.00
        cart.add(_product(id=2, price_sell=1550), Decimal("3"))  # 46.50
        view = cart.view()
        # подытог — точная сумма строк до копейки
        assert view.subtotal == sum(line.total for line in view.lines) == 8650
        # итог округлён вверх до целой ₽, разница вынесена в rounding
        assert view.grand_total == 8700
        assert view.rounding == 50

    def test_grand_total_no_rounding_when_whole(self):
        cart = Cart()
        cart.add(_product(price_sell=2000), Decimal("2"))  # 40.00 ровно
        view = cart.view()
        assert view.subtotal == 4000
        assert view.grand_total == 4000
        assert view.rounding == 0

    def test_empty_cart_totals(self):
        cart = Cart()
        view = cart.view()
        assert view.subtotal == 0
        assert view.grand_total == 0
        assert view.rounding == 0


# --- HTTP-слой ------------------------------------------------------------

def _make_product(db, **overrides):
    data = dict(
        name="Хлеб", price_buy="10.00", price_sell="20.00",
        quantity="50", unit=UnitEnum.kg.value if overrides.get("weighted") else "шт",
    )
    overrides.pop("weighted", None)
    data.update(overrides)
    return create_product(ProductCreate(**data), db)


class TestCashierRoutes:
    def test_screen_renders(self, client):
        resp = client.get("/cashier")
        assert resp.status_code == 200
        assert "Касса" in resp.text

    def test_add_by_numeric_code(self, db, client):
        product = _make_product(db)
        resp = client.post("/cashier/items", data={"numeric_code": product.numeric_code})
        assert resp.status_code == 200
        assert product.name in resp.text
        assert "Итого" in resp.text

    def test_add_by_qr_code(self, db, client):
        # сканер печатает QR-код в то же поле — товар должен найтись и добавиться
        product = _make_product(db, qr_code="4600000012345")
        resp = client.post("/cashier/items", data={"numeric_code": product.qr_code})
        assert resp.status_code == 200
        assert product.name in resp.text
        assert "Итого" in resp.text

    def test_add_unknown_code_shows_message(self, db, client):
        resp = client.post("/cashier/items", data={"numeric_code": "999999"})
        assert resp.status_code == 200
        assert "не найден" in resp.text
        assert "Чек пуст" in resp.text  # корзина не пострадала

    def test_add_same_product_by_scan_merges(self, db, client):
        product = _make_product(db)
        client.post("/cashier/items", data={"numeric_code": product.numeric_code})
        resp = client.post("/cashier/items", data={"numeric_code": product.numeric_code})
        # объединение дублей: одна строка, количество 2 (механика 1.1)
        assert resp.text.count("✕ убрать") == 1

    def test_cashier_does_not_touch_stock(self, db, client):
        product = _make_product(db)
        before = product.quantity_current
        client.post("/cashier/items", data={"numeric_code": product.numeric_code})
        db.refresh(product)
        assert product.quantity_current == before

    def test_clear_empties_cart(self, db, client):
        product = _make_product(db)
        client.post("/cashier/items", data={"numeric_code": product.numeric_code})
        resp = client.post("/cashier/clear")
        assert "Чек пуст" in resp.text


class TestCashierSearch:
    def test_search_finds_by_name(self, db, client):
        product = _make_product(db, name="Молоко 3.2%")
        resp = client.get("/cashier/search", params={"q": "мол"})
        assert resp.status_code == 200
        assert product.name in resp.text
        assert product.status.value in resp.text  # статус виден (разд. 3.4)

    def test_search_empty_query_no_results(self, db, client):
        _make_product(db, name="Молоко")
        resp = client.get("/cashier/search", params={"q": ""})
        assert resp.status_code == 200
        assert "Молоко" not in resp.text
        assert "Ничего не найдено" not in resp.text  # пустой запрос — не «не найдено»

    def test_search_no_match_shows_message(self, db, client):
        _make_product(db, name="Хлеб")
        resp = client.get("/cashier/search", params={"q": "зонтик"})
        assert "Ничего не найдено" in resp.text

    def test_add_to_cart_from_search_result(self, db, client):
        product = _make_product(db, name="Сыр")
        resp = client.post("/cashier/items", data={"numeric_code": product.numeric_code})
        assert resp.status_code == 200
        assert product.name in resp.text
        assert "Итого" in resp.text

    def test_search_does_not_touch_stock(self, db, client):
        product = _make_product(db, name="Молоко")
        before = product.quantity_current
        client.get("/cashier/search", params={"q": "мол"})
        db.refresh(product)
        assert product.quantity_current == before
