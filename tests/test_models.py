import datetime as _dt
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.counter import ProductCodeCounter
from app.models.movement import Movement, OperationType
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus, UnitEnum


def _product(**kwargs) -> Product:
    defaults = {
        "name": "Тест товар",
        "article": "ART-001",
        "numeric_code": "00001",
        "price_sell": 10000,
        "price_buy": 7000,
        "unit": UnitEnum.piece,
        "min_stock": Decimal("1.000"),
    }
    defaults.update(kwargs)
    return Product(**defaults)


class TestCounter:
    def test_counter_seeded(self, db: Session):
        counter = db.get(ProductCodeCounter, 1)
        assert counter is not None
        assert counter.last_value == 0

    def test_seeding_idempotent(self, test_engine):
        from app.database import init_db

        init_db()
        init_db()
        with Session(test_engine) as s:
            rows = s.exec(select(ProductCodeCounter)).all()
        assert len(rows) == 1
        assert rows[0].last_value == 0


class TestProduct:
    def test_create_minimal(self, db: Session):
        p = _product()
        db.add(p)
        db.commit()
        db.refresh(p)
        assert p.id is not None

    def test_default_status_active(self, db: Session):
        p = _product()
        db.add(p)
        db.commit()
        db.refresh(p)
        assert p.status == ProductStatus.active

    def test_price_fields_are_int(self, db: Session):
        p = _product(price_sell=14850, price_buy=9900)
        db.add(p)
        db.commit()
        db.refresh(p)
        assert p.price_sell == 14850
        assert isinstance(p.price_sell, int)

    def test_quantity_current_default_zero(self, db: Session):
        p = _product()
        db.add(p)
        db.commit()
        db.refresh(p)
        assert p.quantity_current == Decimal("0")
        assert isinstance(p.quantity_current, Decimal)

    def test_min_stock_is_decimal(self, db: Session):
        p = _product(min_stock=Decimal("2.500"))
        db.add(p)
        db.commit()
        db.refresh(p)
        assert p.min_stock == Decimal("2.500")
        assert isinstance(p.min_stock, Decimal)

    def test_numeric_code_unique(self, db: Session):
        db.add(_product(name="А", numeric_code="00001"))
        db.add(_product(name="Б", article="ART-002", numeric_code="00001"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_qr_code_unique_non_null(self, db: Session):
        db.add(_product(numeric_code="00001", qr_code="BAR001"))
        db.add(_product(numeric_code="00002", article="ART-002", qr_code="BAR001"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_qr_code_multiple_nulls_allowed(self, db: Session):
        db.add(_product(numeric_code="00001", qr_code=None))
        db.add(_product(numeric_code="00002", article="ART-002", qr_code=None))
        db.commit()

    def test_archived_status(self, db: Session):
        p = _product(status=ProductStatus.archived)
        db.add(p)
        db.commit()
        db.refresh(p)
        assert p.status == ProductStatus.archived


class TestMovement:
    def test_create_movement(self, db: Session):
        p = _product()
        db.add(p)
        db.commit()
        db.refresh(p)

        m = Movement(
            product_id=p.id,
            datetime=_dt.datetime(2026, 6, 1, 10, 0),
            quantity=Decimal("5.000"),
            operation_type=OperationType.income,
        )
        db.add(m)
        db.commit()
        db.refresh(m)
        assert m.id is not None
        assert m.quantity == Decimal("5.000")
        assert isinstance(m.quantity, Decimal)

    def test_movement_fk_enforced(self, db: Session):
        m = Movement(
            product_id=9999,
            datetime=_dt.datetime(2026, 6, 1, 10, 0),
            quantity=Decimal("1.000"),
            operation_type=OperationType.sale,
        )
        db.add(m)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_sale_quantity_negative(self, db: Session):
        p = _product()
        db.add(p)
        db.commit()
        db.refresh(p)

        m = Movement(
            product_id=p.id,
            datetime=_dt.datetime(2026, 6, 1, 10, 0),
            quantity=Decimal("-3.000"),
            operation_type=OperationType.sale,
        )
        db.add(m)
        db.commit()
        db.refresh(m)
        assert m.quantity == Decimal("-3.000")


class TestPriceHistory:
    def test_create_price_history(self, db: Session):
        p = _product()
        db.add(p)
        db.commit()
        db.refresh(p)

        ph = PriceHistory(
            product_id=p.id,
            datetime=_dt.datetime(2026, 6, 1, 9, 0),
            price_buy=7000,
            price_sell=10000,
        )
        db.add(ph)
        db.commit()
        db.refresh(ph)
        assert ph.id is not None
        assert ph.price_buy == 7000
        assert ph.price_sell == 10000

    def test_price_history_fk_enforced(self, db: Session):
        ph = PriceHistory(
            product_id=9999,
            datetime=_dt.datetime(2026, 6, 1, 9, 0),
            price_buy=7000,
            price_sell=10000,
        )
        db.add(ph)
        with pytest.raises(IntegrityError):
            db.commit()
