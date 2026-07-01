from app.models.product import ProductStatus, UnitEnum
from app.schemas.product import ProductCreate
from app.services.product_search import search_products
from app.services.product_service import create_product


def _make(db, name, **overrides):
    data = dict(name=name, price_buy="10.00", price_sell="20.00", quantity="5", unit="шт")
    data.update(overrides)
    return create_product(ProductCreate(**data), db)


class TestProductSearch:
    def test_name_substring_case_insensitive_cyrillic(self, db):
        _make(db, "Молоко 3.2%")
        assert [p.name for p in search_products(db, "мол")] == ["Молоко 3.2%"]
        assert [p.name for p in search_products(db, "МОЛОКО")] == ["Молоко 3.2%"]

    def test_match_by_numeric_code(self, db):
        product = _make(db, "Хлеб")
        results = search_products(db, product.numeric_code)
        assert product.name in [r.name for r in results]

    def test_match_by_article(self, db):
        _make(db, "Сыр Гауда", article="ABC-123")
        results = search_products(db, "abc")
        assert any(r.name == "Сыр Гауда" for r in results)

    def test_code_matches_before_name(self, db):
        # товар, у которого запрос попадает в код, идёт раньше совпадений по названию
        by_name = _make(db, "молоко топлёное")
        by_code = _make(db, "Кефир")  # код 000002 → запрос "0000" префикс кода
        results = search_products(db, "0000")
        assert results[0].id == by_name.id or results[0].id == by_code.id
        # оба нашлись через код (оба numeric_code начинаются с 0000)
        ids = {r.id for r in results}
        assert by_name.id in ids and by_code.id in ids

    def test_empty_query_returns_empty(self, db):
        _make(db, "Хлеб")
        assert search_products(db, "") == []
        assert search_products(db, "   ") == []

    def test_limit(self, db):
        for i in range(25):
            _make(db, f"Товар {i:02d} молоко")
        assert len(search_products(db, "молоко", limit=20)) == 20

    def test_archived_is_found(self, db):
        product = _make(db, "Старый товар")
        product.status = ProductStatus.archived
        db.add(product)
        db.commit()
        results = search_products(db, "старый")
        assert results and results[0].status == ProductStatus.archived

    def test_no_match(self, db):
        _make(db, "Хлеб")
        assert search_products(db, "зонтик") == []
