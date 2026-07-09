"""Навигационная оболочка (этап 2.5) — серверный слой рамы вкладок.

Критерии приёмки: `docs/specs/тз-2.5-навигационная-оболочка.md`, разд. 9 (серверный слой).
Интерактивная часть (переключение вкладок, сохранность поля) — на стороне `shell.js`,
здесь не покрывается: TestClient не исполняет JS.
"""

import pytest

from app.routes.shell import SECTIONS
from app.services.cart import get_cart


@pytest.fixture(autouse=True)
def _reset_cart():
    """Рама встраивает живую корзину-синглтон — чистим до и после каждого теста."""
    get_cart().clear()
    yield
    get_cart().clear()


class TestShellPage:
    def test_root_renders_frame(self, client):
        # Рама занимает корень `/` (окно pywebview открывает именно его).
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text
        # Каркас вкладок и лаунчеров на месте.
        assert 'id="tabs"' in body
        assert 'id="launcher"' in body
        assert "/static/shell.js" in body

    def test_root_embeds_cashier(self, client):
        # Экран кассы встроен в раму на сервере (постоянная стартовая вкладка).
        resp = client.get("/")
        body = resp.text
        assert "Касса" in body
        assert 'id="panel-cashier"' in body
        assert 'id="cart"' in body  # встроенный фрагмент корзины

    def test_root_lists_all_sections(self, client):
        # Кнопка-лаунчер на каждый раздел из белого списка.
        body = client.get("/").text
        for title in SECTIONS.values():
            assert title in body


class TestSectionPanels:
    def test_no_panel_has_test_field(self, client):
        # Тестовое поле рамы (2.5) убрано с наполнением «Добавить» (6.1) — больше нигде.
        for key in SECTIONS:
            resp = client.get(f"/panels/{key}")
            assert resp.status_code == 200
            assert "add-test-field" not in resp.text

    def test_panels_are_stubs(self, client):
        # Разделы без наполнения — заглушки (приходят на своих этапах). «Товары» — 3.1,
        # «Заявки» — 5.3, «Добавить» — 6.1 наполнены, поэтому исключены; остаётся «Чеки».
        for key in SECTIONS:
            if key in ("products", "orders", "add"):
                continue
            assert "в разработке" in client.get(f"/panels/{key}").text

    def test_products_panel_is_real_search(self, client):
        # «Товары» — реальная панель поиска для карточки (этап 3.1), не заглушка.
        resp = client.get("/panels/products")
        assert resp.status_code == 200
        assert "в разработке" not in resp.text
        assert "Поиск товара для карточки" in resp.text

    def test_orders_panel_is_real(self, client):
        # «Заявки» — реальная панель пополнения (этап 5.3), не заглушка.
        resp = client.get("/panels/orders")
        assert resp.status_code == 200
        assert "в разработке" not in resp.text
        assert "Кандидаты на заказ" in resp.text

    def test_add_panel_is_real_receiving(self, client):
        # «Добавить» — реальная панель ручного приёма (этап 6.1), не заглушка.
        resp = client.get("/panels/add")
        assert resp.status_code == 200
        assert "в разработке" not in resp.text
        assert "Поиск товара для приёма" in resp.text

    def test_unknown_panel_404(self, client):
        # Ключ не из белого списка — 404, произвольные панели не открываем.
        assert client.get("/panels/bogus").status_code == 404


class TestStaticAndRegression:
    def test_shell_js_served(self, client):
        # Ванильный JS оболочки раздаётся смонтированной статикой.
        resp = client.get("/static/shell.js")
        assert resp.status_code == 200
        assert "openSection" in resp.text

    def test_cashier_standalone_still_works(self, client):
        # Вынос контента кассы в partial не сломал standalone-страницу /cashier.
        resp = client.get("/cashier")
        assert resp.status_code == 200
        assert "Касса" in resp.text
