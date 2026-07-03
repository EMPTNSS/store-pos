"""Формирование текста чека (этап 2.1). Чистая функция — БД и железо не нужны."""

import datetime as _dt
from decimal import Decimal

from app.models.product import UnitEnum
from app.models.receipt import PaymentMethod, Receipt, ReceiptLine
from app.services.receipt_render import render_receipt_text

WIDTH = 48
_WHEN = _dt.datetime(2026, 7, 3, 14, 22)


def _receipt(**overrides) -> Receipt:
    data = dict(
        receipt_number=1,
        datetime=_WHEN,
        payment_method=PaymentMethod.cash,
        subtotal=16999,
        rounding=1,
        total=17000,
    )
    data.update(overrides)
    return Receipt(**data)


def _line(**overrides) -> ReceiptLine:
    data = dict(
        receipt_id=1,
        product_id=1,
        name="Хлеб бородинский",
        unit=UnitEnum.piece,
        price_sell=4500,
        quantity=Decimal("2"),
        total=9000,
    )
    data.update(overrides)
    return ReceiptLine(**data)


def test_all_blocks_present():
    """Состав по макету 18.4: шапка, товар+сумма, ИТОГО, оплата, дата, номер."""
    text = render_receipt_text(_receipt(), [_line()])
    assert "МАГАЗИН" in text                 # блок магазина
    assert "Чек №0001" in text               # номер чека
    assert "Хлеб бородинский" in text        # список товаров
    assert "90.00" in text                   # сумма по строке
    assert "ИТОГО" in text                   # итоговая сумма
    assert "170.00" in text
    assert "наличные" in text                # способ оплаты (рус. метка)
    assert "2026-07-03 14:22" in text        # дата и время


def test_no_line_exceeds_width():
    """Ни одна строка не длиннее ширины ленты — даже на длинном имени и большой сумме."""
    receipt = _receipt(subtotal=123456789, rounding=0, total=123456789)
    line = _line(
        name="Очень длинное название товара которое заведомо не влезает в ленту 80мм",
        price_sell=9999999,
        quantity=Decimal("12"),
        total=123456789,
    )
    text = render_receipt_text(receipt, [line], width=WIDTH)
    for row in text.splitlines():
        assert len(row) <= WIDTH, f"строка длиннее {WIDTH}: {row!r}"


def test_amounts_taken_as_is_and_formatted():
    """Суммы = зафиксированные значения чека, формат '148.50', без пересчёта."""
    receipt = _receipt(subtotal=16999, rounding=1, total=17000)
    text = render_receipt_text(receipt, [_line(total=9000)])
    assert "169.99" in text   # subtotal
    assert "170.00" in text   # total
    assert "90.00" in text    # line total


def test_rounding_line_shown_only_when_positive():
    with_rounding = render_receipt_text(
        _receipt(subtotal=16999, rounding=1, total=17000), [_line()]
    )
    assert "Округление" in with_rounding

    no_rounding = render_receipt_text(
        _receipt(subtotal=17000, rounding=0, total=17000), [_line()]
    )
    assert "Округление" not in no_rounding


def test_quantity_without_trailing_zeros():
    text = render_receipt_text(
        _receipt(),
        [
            _line(name="Штучный", unit=UnitEnum.piece, quantity=Decimal("2")),
            _line(name="Весовой", unit=UnitEnum.kg, quantity=Decimal("1.5")),
            _line(name="Десяток", unit=UnitEnum.piece, quantity=Decimal("10")),
        ],
    )
    assert "2 шт x" in text
    assert "1.5 кг x" in text
    assert "10 шт x" in text
    assert "2.000" not in text
    assert "1.500" not in text


def test_payment_method_label():
    cash = render_receipt_text(_receipt(payment_method=PaymentMethod.cash), [_line()])
    card = render_receipt_text(_receipt(payment_method=PaymentMethod.card), [_line()])
    assert "Оплата: наличные" in cash
    assert "Оплата: безналичные" in card
