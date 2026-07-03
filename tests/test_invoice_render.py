"""Формирование текста накладной (этап 2.2). Чистая функция — БД и железо не нужны."""

import datetime as _dt
from decimal import Decimal

from app.models.product import UnitEnum
from app.models.receipt import Receipt, ReceiptLine
from app.services.invoice_render import render_invoice_text

WIDTH = 80
_WHEN = _dt.datetime(2026, 7, 3, 14, 22)


def _receipt(**overrides) -> Receipt:
    from app.models.receipt import PaymentMethod

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
    """Состав по макету 18.6: заголовок с номером, товар, кол-во, цена, сумма, итог, дата."""
    text = render_invoice_text(_receipt(), [_line()])
    assert "НАКЛАДНАЯ к чеку №0001" in text   # заголовок + номер чека
    assert "Хлеб бородинский" in text         # список товаров
    assert "2 шт" in text                     # количество с единицей
    assert "45.00" in text                    # цена по строке
    assert "90.00" in text                    # сумма по строке
    assert "ИТОГО" in text                    # итоговая сумма
    assert "170.00" in text
    assert "2026-07-03 14:22" in text         # дата и время покупки


def test_no_line_exceeds_width():
    """Ни одна строка не длиннее ширины документа — даже на длинном имени и большой сумме."""
    receipt = _receipt(subtotal=123456789, rounding=0, total=123456789)
    line = _line(
        name="Очень длинное название товара которое заведомо не влезает в одну колонку",
        price_sell=9999999,
        quantity=Decimal("12"),
        total=123456789,
    )
    text = render_invoice_text(receipt, [line], width=WIDTH)
    for row in text.splitlines():
        assert len(row) <= WIDTH, f"строка длиннее {WIDTH}: {row!r}"


def test_amounts_taken_as_is_and_formatted():
    """Суммы = зафиксированные значения чека, формат '148.50', без пересчёта."""
    receipt = _receipt(subtotal=16999, rounding=1, total=17000)
    text = render_invoice_text(receipt, [_line(price_sell=4500, total=9000)])
    assert "45.00" in text    # цена по строке
    assert "90.00" in text    # сумма по строке
    assert "170.00" in text   # итог = receipt.total


def test_rounding_line_shown_only_when_positive():
    with_rounding = render_invoice_text(
        _receipt(subtotal=16999, rounding=1, total=17000), [_line()]
    )
    assert "Округление" in with_rounding

    no_rounding = render_invoice_text(
        _receipt(subtotal=17000, rounding=0, total=17000), [_line()]
    )
    assert "Округление" not in no_rounding


def test_quantity_without_trailing_zeros():
    text = render_invoice_text(
        _receipt(),
        [
            _line(name="Штучный", unit=UnitEnum.piece, quantity=Decimal("2")),
            _line(name="Весовой", unit=UnitEnum.kg, quantity=Decimal("1.5")),
        ],
    )
    assert "2 шт" in text
    assert "1.5 кг" in text
    assert "2.000" not in text
    assert "1.500" not in text


def test_no_payment_method_in_invoice():
    """Способа оплаты в накладной нет (макет 18.6, в отличие от чека 18.4)."""
    text = render_invoice_text(_receipt(), [_line()])
    assert "Оплата" not in text
    assert "наличные" not in text
    assert "безналичные" not in text
