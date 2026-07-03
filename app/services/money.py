"""Денежные вычисления. Деньги — всегда int в копейках, никогда float."""

from decimal import Decimal, ROUND_HALF_UP

# Копеек в одной денежной единице (₽).
KOPECKS_PER_UNIT = 100


def format_money(kopecks: int) -> str:
    """Копейки → строка вида '148.50' для отображения и печати.

    Единый формат денег для интерфейса (Jinja-фильтр `money`) и печати чека,
    чтобы вид суммы был один во всей системе.
    """
    return f"{kopecks / KOPECKS_PER_UNIT:.2f}"


def format_quantity(quantity: Decimal) -> str:
    """Количество без лишних нулей: 2 (шт), 1.5 (кг), 10 (шт).

    Единый формат количества для печати чека и накладной. Фиксированная точка,
    без экспоненты (10, а не 1E+1); дробную часть без значащих нулей отбрасываем.
    """
    text = format(quantity, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def line_total(price_kopecks: int, quantity: Decimal) -> int:
    """Сумма строки чека = цена × количество, округлённая до целой копейки.

    Цена приходит в копейках (int), количество — Decimal (штучное или весовое).
    Округление ROUND_HALF_UP — то же правило, что и при разборе цен
    (см. ``app.schemas.product._parse_kopecks``). Результат — снова int в копейках.
    """
    product = Decimal(price_kopecks) * Decimal(quantity)
    return int(product.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def round_total_up(kopecks: int) -> int:
    """Округлить итог чека ВВЕРХ до целой денежной единицы (₽).

    Строки чека точны до копейки, но итоговую сумму округляем вверх до целой ₽:
    покупатель платит ровную сумму без копеечной сдачи, переплата ≤ 1 ₽ на чек и
    всегда в пользу магазина (решение по открытому вопросу округления). Разница
    (`grand_total - subtotal`) показывается в чеке отдельной строкой «Округление».
    """
    # Деление с округлением вверх; для 0 даёт 0.
    units = -(-kopecks // KOPECKS_PER_UNIT)
    return units * KOPECKS_PER_UNIT
