"""Подключение поставщиков к созданию товара (макет 11.9, этап 5.3 UI).

Проверяем слой сервиса (create_product + resolve_suppliers), схему и HTTP-путь.
"""

import pytest
from sqlmodel import Session, select

from app.models.counter import ProductCodeCounter
from app.models.product import Product, UnitEnum
from app.models.supplier import ProductSupplierLink, Supplier, SupplierStatus
from app.schemas.product import ProductCreate
from app.services.product_service import create_product


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


def _links(session: Session, product_id: int) -> list[ProductSupplierLink]:
    return session.exec(
        select(ProductSupplierLink).where(
            ProductSupplierLink.product_id == product_id
        )
    ).all()


# ── 1. Товар без поставщиков → 0 связей ──────────────────────────────────────
def test_no_suppliers(db: Session):
    product = create_product(_data(), db)
    assert _links(db, product.id) == []
    assert db.exec(select(Supplier)).all() == []


# ── 2. Один новый поставщик → создан + 1 связь ───────────────────────────────
def test_single_new_supplier(db: Session):
    product = create_product(_data(supplier_names=["Кола Компани"]), db)

    suppliers = db.exec(select(Supplier)).all()
    assert len(suppliers) == 1
    assert suppliers[0].name == "Кола Компани"
    assert suppliers[0].status == SupplierStatus.active

    links = _links(db, product.id)
    assert len(links) == 1
    assert links[0].supplier_id == suppliers[0].id


# ── 3. Несколько новых поставщиков → все созданы + N связей ───────────────────
def test_multiple_new_suppliers(db: Session):
    names = ["Альфа", "Бета", "Гамма"]
    product = create_product(_data(supplier_names=names), db)

    assert len(db.exec(select(Supplier)).all()) == 3
    assert len(_links(db, product.id)) == 3


# ── 4. Реюз существующего по name_key (регистр/пробелы) → дубля нет ───────────
def test_reuse_existing_by_name_key(db: Session):
    existing = Supplier(name="Кола", name_key="кола", status=SupplierStatus.active)
    db.add(existing)
    db.commit()
    db.refresh(existing)

    product = create_product(_data(supplier_names=["  КОЛА  "]), db)

    suppliers = db.exec(select(Supplier)).all()
    assert len(suppliers) == 1, "дубль не должен создаваться"

    links = _links(db, product.id)
    assert len(links) == 1
    assert links[0].supplier_id == existing.id


# ── 5. Дедуп в пределах одной формы (один поставщик дважды) → одна связь ──────
def test_dedup_within_form(db: Session):
    product = create_product(_data(supplier_names=["Кола", "кола "]), db)

    assert len(db.exec(select(Supplier)).all()) == 1
    assert len(_links(db, product.id)) == 1


# ── 6. Смесь существующий + новый → 1 создан, 2 связи ────────────────────────
def test_mix_existing_and_new(db: Session):
    existing = Supplier(name="Альфа", name_key="альфа", status=SupplierStatus.active)
    db.add(existing)
    db.commit()

    product = create_product(_data(supplier_names=["Альфа", "Бета"]), db)

    assert len(db.exec(select(Supplier)).all()) == 2  # +1 создан (Бета)
    assert len(_links(db, product.id)) == 2


# ── 7. Пустые ряды игнорируются схемой → 0 поставщиков ───────────────────────
def test_empty_rows_ignored(db: Session):
    data = _data(supplier_names=["", "   ", ""])
    assert data.supplier_names == []  # отсечены валидатором схемы

    product = create_product(data, db)
    assert db.exec(select(Supplier)).all() == []
    assert _links(db, product.id) == []


# ── 8. Атомарность: сбой после разрешения поставщиков → полный rollback ───────
def test_atomicity_rollback_includes_new_suppliers(test_engine, monkeypatch):
    from app.database import init_db
    import app.services.product_service as svc

    init_db()

    # Ломаем шаг вставки связи — уже после создания новых Supplier во flush.
    class _FailingLink:
        def __init__(self, **kwargs):
            raise RuntimeError("simulated failure at link step")

    monkeypatch.setattr(svc, "ProductSupplierLink", _FailingLink)

    with pytest.raises(RuntimeError, match="simulated failure"):
        with Session(test_engine) as s:
            create_product(_data(supplier_names=["Новый Поставщик"]), s)

    with Session(test_engine) as s:
        assert s.exec(select(Product)).all() == [], "товар откачен"
        assert s.exec(select(Supplier)).all() == [], "новый поставщик откачен"
        assert s.exec(select(ProductSupplierLink)).all() == [], "связи откачены"
        counter = s.get(ProductCodeCounter, 1)
        assert counter.last_value == 0, "счётчик не сдвинулся"


# ── 9. Схема: очистка supplier_names (trim, пустые, отсутствие поля) ──────────
def test_schema_cleans_supplier_names():
    assert _data(supplier_names=["  Кола  ", "", "  ", "Бета"]).supplier_names == [
        "Кола",
        "Бета",
    ]
    assert _data().supplier_names == []  # поле опционально


# ── 10. HTTP: POST /products с несколькими supplier → 303, связи созданы ──────
def test_http_post_with_suppliers(client, test_engine):
    resp = client.post(
        "/products",
        data={
            "name": "HTTP Товар",
            "price_buy": "30.00",
            "price_sell": "60.00",
            "quantity": "10",
            "unit": "шт",
            "supplier": ["Поставщик Один", "Поставщик Два", ""],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    with Session(test_engine) as s:
        product = s.exec(select(Product)).one()
        assert len(_links(s, product.id)) == 2
        assert len(s.exec(select(Supplier)).all()) == 2


# ── 11. HTTP: GET /products/supplier-row → фрагмент с input name="supplier" ───
def test_http_supplier_row_fragment(client):
    resp = client.get("/products/supplier-row")
    assert resp.status_code == 200
    assert 'name="supplier"' in resp.text


# ── 12. HTTP: GET /products/new содержит datalist и активных поставщиков ──────
def test_http_new_form_has_datalist(client, test_engine):
    with Session(test_engine) as s:
        s.add(Supplier(name="Видимый", name_key="видимый", status=SupplierStatus.active))
        s.add(Supplier(name="Скрытый", name_key="скрытый", status=SupplierStatus.archived))
        s.commit()

    resp = client.get("/products/new")
    assert resp.status_code == 200
    assert "<datalist" in resp.text
    assert "Видимый" in resp.text
    assert "Скрытый" not in resp.text, "архивные не попадают в список выбора"
