"""Формирование текста чека (этап 2.1). Слой «ЧТО печатать».

Чистая функция: из сохранённого ``Receipt`` и его строк собирает текст чека шириной
48 символов (лента 80 мм, Шрифт A). Без ввода-вывода и без БД — состав чека (макет 18.4)
тестируется без железа. Транспорт (куда печатать) живёт отдельно в ``app/hardware``.

Суммы берутся из чека как есть и **не пересчитываются** (правило 4 CLAUDE.md): печатаем
ровно тот снимок, что сохранён при продаже.
"""

import textwrap

from app.config import settings
from app.models.receipt import Receipt, ReceiptLine
from app.services.money import format_money, format_quantity


def _center(text: str, width: int) -> str:
    """Отцентрировать строку по ширине ленты (длинную — обрезать)."""
    return text[:width].center(width)


def _lr(left: str, right: str, width: int) -> str:
    """Левый текст + правый текст, прижатый к правому краю ширины.

    Если вместе не помещаются — обрезаем левую часть (правая — короткая сумма).
    """
    gap = width - len(left) - len(right)
    if gap < 1:
        left = left[: max(0, width - len(right) - 1)]
        gap = max(1, width - len(left) - len(right))
    return f"{left}{' ' * gap}{right}"


def render_receipt_text(
    receipt: Receipt,
    lines: list[ReceiptLine],
    width: int | None = None,
) -> str:
    """Собрать текст чека по составу макета 18.4. Ширина по умолчанию — из конфига."""
    width = settings.receipt_line_width if width is None else width
    separator = "-" * width
    out: list[str] = []

    # Блок магазина / рекламный блок (может быть многострочным).
    for header_line in settings.receipt_header.splitlines():
        out.append(_center(header_line, width))
    out.append(_center(f"Чек №{receipt.receipt_number:04d}", width))
    out.append(separator)

    # Список товаров: название на своей строке, ниже «кол-во ед x цена ... сумма».
    for line in lines:
        for name_line in textwrap.wrap(line.name, width) or [""]:
            out.append(name_line)
        qty = format_quantity(line.quantity)
        left = f"  {qty} {line.unit.value} x {format_money(line.price_sell)}"
        out.append(_lr(left, format_money(line.total), width))

    out.append(separator)

    # Итоги: подытог, округление (если есть), ИТОГО.
    out.append(_lr("Итог по строкам", format_money(receipt.subtotal), width))
    if receipt.rounding > 0:
        out.append(_lr("Округление", format_money(receipt.rounding), width))
    out.append(_lr("ИТОГО", format_money(receipt.total), width))

    # Способ оплаты, дата и время покупки.
    out.append(f"Оплата: {receipt.payment_method.value}")
    out.append(receipt.datetime.strftime("%Y-%m-%d %H:%M"))

    if settings.receipt_footer:
        out.append(_center(settings.receipt_footer, width))

    return "\n".join(out)
