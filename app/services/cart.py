"""Корзина текущего (незавершённого) чека.

Одна касса = один активный чек, поэтому состояние держим в памяти процесса
(модуль-синглтон ``_cart``). БД здесь не трогаем: чек не сохраняется и остаток не
списывается до завершения продажи (пункт 1.3). Цена позиции снимается с товара в момент
добавления и дальше не пересчитывается, даже если карточка товара изменится.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.models.product import Product, UnitEnum
from app.services.money import line_total, round_total_up


@dataclass
class CartLine:
    line_id: int
    product_id: int
    name: str
    unit: UnitEnum
    price_sell: int  # снимок цены в копейках на момент добавления
    quantity: Decimal

    @property
    def total(self) -> int:
        """Сумма строки в копейках."""
        return line_total(self.price_sell, self.quantity)


@dataclass
class CartView:
    lines: list[CartLine]
    subtotal: int      # сумма строк в копейках (точно до копейки)
    rounding: int      # надбавка округления итога до целой ₽ (≥ 0)
    grand_total: int   # итог к оплате в копейках (subtotal, округлённый вверх до ₽)


@dataclass
class Cart:
    _lines: dict[int, CartLine] = field(default_factory=dict)
    _next_id: int = 1

    def add(self, product: Product, quantity: Decimal = Decimal("1")) -> CartLine:
        """Добавить товар. Если товар уже в чеке — увеличить количество его строки."""
        for line in self._lines.values():
            if line.product_id == product.id:
                line.quantity += quantity
                return line

        line = CartLine(
            line_id=self._next_id,
            product_id=product.id,
            name=product.name,
            unit=product.unit,
            price_sell=product.price_sell,
            quantity=quantity,
        )
        self._lines[line.line_id] = line
        self._next_id += 1
        return line

    def set_quantity(self, line_id: int, quantity: Decimal) -> Optional[CartLine]:
        """Заменить количество строки. Количество должно быть больше 0."""
        if quantity <= Decimal("0"):
            raise ValueError("Количество должно быть больше 0")
        line = self._lines.get(line_id)
        if line is not None:
            line.quantity = quantity
        return line

    def remove(self, line_id: int) -> None:
        self._lines.pop(line_id, None)

    def clear(self) -> None:
        """Очистить чек целиком (отмена незавершённой продажи, разд. 15.1)."""
        self._lines.clear()
        self._next_id = 1

    def view(self) -> CartView:
        lines = list(self._lines.values())
        subtotal = sum(line.total for line in lines)
        grand_total = round_total_up(subtotal)
        return CartView(
            lines=lines,
            subtotal=subtotal,
            rounding=grand_total - subtotal,
            grand_total=grand_total,
        )


# Единственный активный чек кассы.
_cart = Cart()


def get_cart() -> Cart:
    return _cart
