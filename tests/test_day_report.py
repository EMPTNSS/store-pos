"""Тесты документа завершения рабочего дня (этап 7.1, макет разд. 23).

Проверяем денежную/складскую арифметику дня (себестоимость на момент операции, вычет
возвратов, потоварный агрегат), отбор операций строго по смене (``work_day_id``),
атомарное закрытие смены с формированием документа и сохранение/чтение.
"""

import datetime as _dt
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlmodel import select

from app.models.day_report import DayReport, DayReportLine
from app.models.price_history import PriceHistory
from app.models.receipt import PaymentMethod, Receipt
from app.models.work_day import WorkDayStatus
from app.schemas.product import ProductCreate
from app.services.cart import Cart
from app.services.day_report_service import (
    close_day_and_report,
    compute_day,
    day_report_lines,
    get_day_report,
    list_day_reports,
)
from app.services.product_service import create_product
from app.services.return_cart import ReturnCart
from app.services.return_service import complete_return
from app.services.sale import complete_sale
from app.services.work_day_service import open_day


def _make_product(db, **overrides):
    data = dict(
        name="Хлеб", price_buy="10.00", price_sell="20.00",
        quantity="100", unit="шт",
    )
    data.update(overrides)
    return create_product(ProductCreate(**data), db)


def _sell(db, items, payment=PaymentMethod.cash):
    """Продать позиции в открытую смену. items — [(product, Decimal qty), ...]."""
    cart = Cart()
    for product, qty in items:
        cart.add(product, qty)
    return complete_sale(db, cart, payment)


def _return(db, items, payment=PaymentMethod.cash):
    """Оформить возврат в открытую смену."""
    cart = ReturnCart()
    for product, qty in items:
        cart.add(product, qty)
    return complete_return(db, cart, payment)


# --- себестоимость и прибыль (compute_day) --------------------------------

class TestCostAndProfit:
    def test_single_sale_cogs_and_profit(self, db):
        day = open_day(db)
        p = _make_product(db, price_buy="10.00", price_sell="20.00")
        _sell(db, [(p, Decimal("1"))])

        c = compute_day(db, day)
        assert c.sales_total == 2000
        assert c.cogs_sold == 1000
        assert c.net_sales == 2000
        assert c.net_profit == 1000  # 2000 − 1000

    def test_cogs_uses_buy_price_at_sale_time(self, db):
        day = open_day(db)
        p = _make_product(db, price_buy="10.00", price_sell="20.00")
        _sell(db, [(p, Decimal("1"))])

        # Позже закупка выросла — новая точка price_history ПОСЛЕ продажи.
        db.add(PriceHistory(
            product_id=p.id,
            datetime=_dt.datetime.now() + timedelta(hours=1),
            price_buy=3000, price_sell=2000,
        ))
        db.commit()

        c = compute_day(db, day)
        # Берётся закупка, действовавшая на момент чека (10.00), а не текущая 30.00.
        assert c.cogs_sold == 1000

    def test_weight_product_fractional(self, db):
        day = open_day(db)
        p = _make_product(db, price_buy="10.00", price_sell="30.00", unit="кг")
        _sell(db, [(p, Decimal("1.5"))])

        c = compute_day(db, day)
        assert c.sales_total == 4500  # 1.5 × 30.00
        assert c.cogs_sold == 1500    # 1.5 × 10.00
        assert c.net_profit == 3000


# --- возвраты (разд. 2.3) -------------------------------------------------

class TestReturns:
    def test_returns_subtracted_from_totals(self, db):
        day = open_day(db)
        p = _make_product(db, price_buy="10.00", price_sell="20.00")
        _sell(db, [(p, Decimal("2"))])    # продажи 4000, cogs_sold 2000
        _return(db, [(p, Decimal("1"))])  # возврат 2000, cogs_returned 1000

        c = compute_day(db, day)
        assert c.sales_total == 4000
        assert c.returns_total == 2000
        assert c.net_sales == 2000
        assert c.cogs_sold == 2000
        assert c.cogs_returned == 1000
        # net_profit = 2000 − (2000 − 1000) = 1000
        assert c.net_profit == 1000


# --- потоварная детализация -----------------------------------------------

class TestLines:
    def test_product_aggregated_across_receipts(self, db):
        day = open_day(db)
        p1 = _make_product(db, name="Хлеб", price_sell="20.00", price_buy="10.00")
        p2 = _make_product(db, name="Молоко", price_sell="50.00", price_buy="30.00")
        _sell(db, [(p1, Decimal("1"))])
        _sell(db, [(p1, Decimal("2")), (p2, Decimal("1"))])

        c = compute_day(db, day)
        by_id = {line.product_id: line for line in c.lines}
        assert by_id[p1.id].quantity_sold == Decimal("3")
        assert by_id[p1.id].sum_sold == 6000
        assert by_id[p2.id].sum_sold == 5000
        # Сортировка по сумме продаж убыв.: p1 (6000) перед p2 (5000).
        assert [line.product_id for line in c.lines] == [p1.id, p2.id]

    def test_sum_sold_plus_rounding_equals_sales_total(self, db):
        day = open_day(db)
        p = _make_product(db, price_sell="20.33", price_buy="10.00")
        _sell(db, [(p, Decimal("1"))])

        c = compute_day(db, day)
        assert c.sales_total == 2100    # итог округлён вверх до ₽
        assert c.rounding_total == 67   # надбавка 2100 − 2033
        assert sum(line.sum_sold for line in c.lines) + c.rounding_total == c.sales_total


# --- отбор по смене (разд. 2.2) -------------------------------------------

class TestShiftSelection:
    def test_only_current_shift_operations(self, db):
        day1 = open_day(db)
        p = _make_product(db, price_sell="20.00", price_buy="10.00")
        _sell(db, [(p, Decimal("1"))])
        close_day_and_report(db)  # смена 1 закрыта

        day2 = open_day(db)
        _sell(db, [(p, Decimal("3"))])

        # Чек без смены (work_day_id NULL) не попадает ни в одну.
        db.add(Receipt(
            receipt_number=999, datetime=_dt.datetime.now(),
            payment_method=PaymentMethod.cash, subtotal=1000, rounding=0,
            total=1000, work_day_id=None,
        ))
        db.commit()

        c2 = compute_day(db, day2)
        assert c2.sales_total == 6000  # только продажи смены 2
        assert c2.sales_count == 1

        c1 = compute_day(db, day1)
        assert c1.sales_total == 2000  # смена 1 неизменна

    def test_two_shifts_independent_reports(self, db):
        day1 = open_day(db)
        p = _make_product(db, price_sell="20.00", price_buy="10.00")
        _sell(db, [(p, Decimal("1"))])
        r1 = close_day_and_report(db)

        day2 = open_day(db)
        _sell(db, [(p, Decimal("2"))])
        r2 = close_day_and_report(db)

        assert r1.work_day_id == day1.id
        assert r2.work_day_id == day2.id
        assert r1.sales_total == 2000
        assert r2.sales_total == 4000
        assert r2.report_number == r1.report_number + 1


# --- атомарное закрытие (close_day_and_report) ----------------------------

class TestCloseAndReport:
    def test_close_creates_report_and_closes_shift(self, db):
        day = open_day(db)
        p = _make_product(db, price_sell="20.00", price_buy="10.00")
        _sell(db, [(p, Decimal("2"))])

        report = close_day_and_report(db)
        assert report.id is not None
        assert report.work_day_id == day.id

        db.refresh(day)
        assert day.status == WorkDayStatus.closed
        assert day.closed_at is not None
        assert report.opened_at == day.opened_at
        assert report.closed_at == day.closed_at

        lines = day_report_lines(db, report.id)
        assert len(lines) == 1
        assert lines[0].sum_sold == 4000

    def test_close_without_open_day_rejected(self, db):
        with pytest.raises(ValueError):
            close_day_and_report(db)
        assert db.exec(select(DayReport)).all() == []

    def test_close_twice_rejected(self, db):
        open_day(db)
        close_day_and_report(db)
        # Открытой смены больше нет → повторное закрытие отклоняется, второй документ не создаётся.
        with pytest.raises(ValueError):
            close_day_and_report(db)
        assert len(db.exec(select(DayReport)).all()) == 1

    def test_empty_shift_zero_document(self, db):
        open_day(db)
        report = close_day_and_report(db)
        assert report.sales_total == 0
        assert report.returns_total == 0
        assert report.net_sales == 0
        assert report.net_profit == 0
        assert day_report_lines(db, report.id) == []

    def test_report_numbers_sequential(self, db):
        open_day(db)
        r1 = close_day_and_report(db)
        open_day(db)
        r2 = close_day_and_report(db)
        assert r1.report_number == 1
        assert r2.report_number == 2


# --- доп. финансовая информация -------------------------------------------

class TestExtraInfo:
    def test_counts_and_payment_breakdown(self, db):
        day = open_day(db)
        p = _make_product(db, price_sell="20.00", price_buy="10.00")
        _sell(db, [(p, Decimal("1"))], payment=PaymentMethod.cash)   # 2000 нал
        _sell(db, [(p, Decimal("2"))], payment=PaymentMethod.card)   # 4000 безнал
        _return(db, [(p, Decimal("1"))], payment=PaymentMethod.cash) # 2000 нал возврат

        c = compute_day(db, day)
        assert c.sales_count == 2
        assert c.returns_count == 1
        assert c.cash_sales == 2000
        assert c.card_sales == 4000
        assert c.cash_returns == 2000
        assert c.card_returns == 0


# --- сохранение и чтение (разд. 23.5) -------------------------------------

class TestPersistence:
    def test_saved_report_readable_later(self, db):
        open_day(db)
        p = _make_product(db, price_sell="20.00", price_buy="10.00")
        _sell(db, [(p, Decimal("1"))])
        report = close_day_and_report(db)

        fetched = get_day_report(db, report.id)
        assert fetched is not None
        assert fetched.net_profit == report.net_profit

        assert any(r.id == report.id for r in list_day_reports(db))

        saved_lines = db.exec(
            select(DayReportLine).where(DayReportLine.day_report_id == report.id)
        ).all()
        assert len(saved_lines) == 1


# --- HTTP-слой ------------------------------------------------------------

class TestReportRoutes:
    def test_close_route_creates_report(self, db, client):
        client.post("/work-day/open")
        p = _make_product(db, price_sell="20.00", price_buy="10.00")
        _sell(db, [(p, Decimal("1"))])

        resp = client.post("/work-day/close")
        assert resp.status_code == 200
        assert "Документ дня" in resp.text

        reports = db.exec(select(DayReport)).all()
        assert len(reports) == 1
        assert reports[0].sales_total == 2000

    def test_close_route_without_open_day(self, db, client):
        resp = client.post("/work-day/close")
        assert resp.status_code == 200
        assert "Нет открытого рабочего дня" in resp.text
        assert db.exec(select(DayReport)).all() == []

    def test_view_and_list_routes(self, db, client):
        open_day(db)
        p = _make_product(db, price_sell="20.00", price_buy="10.00")
        _sell(db, [(p, Decimal("1"))])
        report = close_day_and_report(db)

        resp = client.get(f"/day/reports/{report.id}")
        assert resp.status_code == 200
        assert f"Документ завершения дня №{report.report_number}" in resp.text

        resp_list = client.get("/day/reports")
        assert resp_list.status_code == 200
        assert f"№{report.report_number}" in resp_list.text

    def test_view_missing_report_404(self, db, client):
        resp = client.get("/day/reports/999")
        assert resp.status_code == 404

    def test_receipts_panel_shows_document(self, db, client):
        open_day(db)
        p = _make_product(db, price_sell="20.00", price_buy="10.00")
        _sell(db, [(p, Decimal("1"))])
        report = close_day_and_report(db)

        resp = client.get("/panels/receipts")
        assert resp.status_code == 200
        assert "Чеки за день" in resp.text
        assert f"№{report.report_number}" in resp.text
