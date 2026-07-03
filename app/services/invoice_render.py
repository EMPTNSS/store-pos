"""Формирование текста накладной (этап 2.2). Слой «ЧТО печатать».

Чистая функция: из сохранённого ``Receipt`` и его строк собирает текст накладной —
табличного документа шириной 80 символов (в отличие от узкой чековой ленты 2.1).
Без ввода-вывода и без БД — состав накладной (макет 18.6) тестируется без железа.
Транспорт (куда печатать) живёт отдельно в ``app/hardware/invoice_printer.py``.

Суммы берутся из чека как есть и **не пересчитываются** (правило 4 CLAUDE.md): накладная —
снимок того же чека, что сохранён при продаже.
"""

import textwrap

from app.config import settings
from app.models.receipt import Receipt, ReceiptLine
from app.services.money import format_money, format_quantity

# Ширины колонок табличной части (в символах). Между колонками — один пробел.
_W_NUM = 3     # порядковый номер строки
_W_QTY = 10    # количество с единицей измерения
_W_PRICE = 12  # цена за единицу
_W_SUM = 12    # сумма строки
_GAPS = 4      # 4 разделительных пробела между 5 колонками


def _lr(left: str, right: str, width: int) -> str:
    """Левый текст + правый, прижатый к правому краю ширины (длинный левый — обрезаем)."""
    gap = width - len(left) - len(right)
    if gap < 1:
        left = left[: max(0, width - len(right) - 1)]
        gap = max(1, width - len(left) - len(right))
    return f"{left}{' ' * gap}{right}"


def _row(num: str, name: str, qty: str, price: str, total: str, name_width: int) -> str:
    """Одна табличная строка: № слева, Товар слева, числовые колонки — справа."""
    return (
        f"{num:<{_W_NUM}} {name:<{name_width}} "
        f"{qty:>{_W_QTY}} {price:>{_W_PRICE}} {total:>{_W_SUM}}"
    )


def render_invoice_text(
    receipt: Receipt,
    lines: list[ReceiptLine],
    width: int | None = None,
) -> str:
    """Собрать текст накладной по составу макета 18.6. Ширина по умолчанию — из конфига."""
    width = settings.invoice_line_width if width is None else width
    name_width = width - (_W_NUM + _W_QTY + _W_PRICE + _W_SUM + _GAPS)
    separator = "-" * width
    out: list[str] = []

    # Шапка: заголовок с номером чека слева, дата и время покупки справа.
    title = f"{settings.invoice_title} к чеку №{receipt.receipt_number:04d}"
    out.append(_lr(title, receipt.datetime.strftime("%Y-%m-%d %H:%M"), width))
    out.append(separator)

    # Заголовок таблицы.
    out.append(_row("№", "Товар", "Кол-во", "Цена", "Сумма", name_width))

    # Строки: список товаров, количество, цена и сумма по каждому (18.6).
    for i, line in enumerate(lines, start=1):
        qty = f"{format_quantity(line.quantity)} {line.unit.value}"
        wrapped = textwrap.wrap(line.name, name_width) or [""]
        out.append(
            _row(
                str(i), wrapped[0], qty,
                format_money(line.price_sell), format_money(line.total), name_width,
            )
        )
        # Длинное название — переносим остаток в колонку «Товар», без чисел.
        for extra in wrapped[1:]:
            out.append(_row("", extra, "", "", "", name_width))

    out.append(separator)

    # Итоговая сумма (18.6). Округление показываем, чтобы итог сходился со строками.
    if receipt.rounding > 0:
        out.append(_lr("Округление", format_money(receipt.rounding), width))
    out.append(_lr("ИТОГО", format_money(receipt.total), width))

    return "\n".join(out)
