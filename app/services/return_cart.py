"""Корзина возврата (этап 4.1, макет разд. 14).

Черновик возврата в памяти — зеркало кассовой ``Cart`` (1.1), но с **редактируемой ценой**
позиции: продавец возвращает «ту сумму, что написана». Отдельный класс, чтобы не менять
протестированный продажный ``Cart`` (безопасность денежного пути). БД до проведения не
трогаем; при закрытии/отмене модалки черновик очищается явно (разовое действие, 2.5).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.models.product import Product, UnitEnum
from app.services.money import line_total


@dataclass
class ReturnCartLine:
    line_id: int
    product_id: int
    name: str
    unit: UnitEnum
    price: int  # цена возврата за единицу в копейках (по умолчанию price_sell, редактируется)
    quantity: Decimal

    @property
    def total(self) -> int:
        """Сумма строки возврата в копейках."""
        return line_total(self.price, self.quantity)


@dataclass
class ReturnCartView:
    lines: list[ReturnCartLine]
    total: int  # итог возврата = Σ строк (без округления вверх до ₽)


@dataclass
class ReturnCart:
    _lines: dict[int, ReturnCartLine] = field(default_factory=dict)
    _next_id: int = 1

    def add(self, product: Product, quantity: Decimal = Decimal("1")) -> ReturnCartLine:
        """Добавить товар. Если товар уже в возврате — увеличить количество его строки."""
        for line in self._lines.values():
            if line.product_id == product.id:
                line.quantity += quantity
                return line

        line = ReturnCartLine(
            line_id=self._next_id,
            product_id=product.id,
            name=product.name,
            unit=product.unit,
            price=product.price_sell,  # снимок цены продажи как значение по умолчанию
            quantity=quantity,
        )
        self._lines[line.line_id] = line
        self._next_id += 1
        return line

    def set_quantity(self, line_id: int, quantity: Decimal) -> Optional[ReturnCartLine]:
        """Заменить количество строки. Количество должно быть больше 0."""
        if quantity <= Decimal("0"):
            raise ValueError("Количество должно быть больше 0")
        line = self._lines.get(line_id)
        if line is not None:
            line.quantity = quantity
        return line

    def set_price(self, line_id: int, price: int) -> Optional[ReturnCartLine]:
        """Заменить цену возврата строки (копейки, ≥ 0)."""
        if price < 0:
            raise ValueError("Цена не может быть отрицательной")
        line = self._lines.get(line_id)
        if line is not None:
            line.price = price
        return line

    def remove(self, line_id: int) -> None:
        self._lines.pop(line_id, None)

    def clear(self) -> None:
        """Очистить возврат целиком (отмена/закрытие модалки, разовое действие 2.5)."""
        self._lines.clear()
        self._next_id = 1

    def view(self) -> ReturnCartView:
        lines = list(self._lines.values())
        return ReturnCartView(lines=lines, total=sum(line.total for line in lines))


# Единственный активный черновик возврата.
_return_cart = ReturnCart()


def get_return_cart() -> ReturnCart:
    return _return_cart
