"""Корзина возврата (этап 4.1, макет разд. 14; расширена в 4.2, разд. 15.2–15.3).

Черновик возврата в памяти — зеркало кассовой ``Cart`` (1.1), но с **редактируемой ценой**
позиции: продавец возвращает «ту сумму, что написана». Отдельный класс, чтобы не менять
протестированный продажный ``Cart`` (безопасность денежного пути). БД до проведения не
трогаем; при закрытии/отмене модалки черновик очищается явно (разовое действие, 2.5).

4.2: строка может быть **корректирующей** — перенесённой из завершённого чека
(``add_from_receipt_line``). У неё зафиксирована цена по чеку (``price_locked``) и есть
привязка к строке чека (``source_line_id``). Корзина держит один чек-первоисточник
(``source_receipt_id``): один возврат = один чек, свободные и корректирующие строки не
смешиваются.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.models.product import Product, UnitEnum
from app.models.receipt import ReceiptLine
from app.services.money import line_total


@dataclass
class ReturnCartLine:
    line_id: int
    product_id: int
    name: str
    unit: UnitEnum
    price: int  # цена возврата за единицу в копейках (по умолчанию price_sell, редактируется)
    quantity: Decimal
    source_line_id: Optional[int] = None  # строка чека-первоисточника (4.2), NULL — свободная
    price_locked: bool = False  # цена зафиксирована по чеку (4.2), не редактируется

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
    source_receipt_id: Optional[int] = None  # чек-первоисточник корректирующего возврата (4.2)

    def add(self, product: Product, quantity: Decimal = Decimal("1")) -> ReturnCartLine:
        """Добавить товар (свободный возврат 4.1). Если товар уже в возврате — увеличить количество.

        Нельзя примешивать свободную позицию к возврату по чеку (4.2): один возврат = один чек.
        """
        if self.source_receipt_id is not None:
            raise ValueError(
                "Идёт возврат по чеку. Очистите корзину, чтобы вернуть товар свободно."
            )
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

    def add_from_receipt_line(
        self, receipt_line: ReceiptLine, quantity: Decimal = Decimal("1")
    ) -> ReturnCartLine:
        """Перенести строку завершённого чека в возврат (корректирующий возврат, 4.2).

        Цена берётся из чека и фиксируется (``price_locked``). Повторное добавление той же
        строки чека — мёрж по ``source_line_id`` (один товар может стоять в чеке несколькими
        строками). Смешивать со свободными строками или строками другого чека нельзя.
        """
        # Свободные строки в корзине (source_receipt_id ещё не выставлен) — нельзя смешивать.
        if self._lines and self.source_receipt_id is None:
            raise ValueError("Очистите корзину: свободный возврат и возврат по чеку не смешиваются.")
        if (
            self.source_receipt_id is not None
            and self.source_receipt_id != receipt_line.receipt_id
        ):
            raise ValueError("Очистите корзину: возврат по чеку привязан к одному чеку.")

        for line in self._lines.values():
            if line.source_line_id == receipt_line.id:
                line.quantity += quantity
                return line

        line = ReturnCartLine(
            line_id=self._next_id,
            product_id=receipt_line.product_id,
            name=receipt_line.name,       # снимок из чека, не из карточки
            unit=receipt_line.unit,
            price=receipt_line.price_sell,  # цена по чеку — зафиксирована
            quantity=quantity,
            source_line_id=receipt_line.id,
            price_locked=True,
        )
        self._lines[line.line_id] = line
        self._next_id += 1
        self.source_receipt_id = receipt_line.receipt_id
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
        """Заменить цену возврата строки (копейки, ≥ 0).

        Для корректирующей строки (``price_locked``, 4.2) цена по чеку не редактируется.
        """
        line = self._lines.get(line_id)
        if line is None:
            return None
        if line.price_locked:
            raise ValueError("Цена по чеку не редактируется")
        if price < 0:
            raise ValueError("Цена не может быть отрицательной")
        line.price = price
        return line

    def remove(self, line_id: int) -> None:
        self._lines.pop(line_id, None)

    def clear(self) -> None:
        """Очистить возврат целиком (отмена/закрытие модалки, разовое действие 2.5)."""
        self._lines.clear()
        self._next_id = 1
        self.source_receipt_id = None

    def view(self) -> ReturnCartView:
        lines = list(self._lines.values())
        return ReturnCartView(lines=lines, total=sum(line.total for line in lines))


# Единственный активный черновик возврата.
_return_cart = ReturnCart()


def get_return_cart() -> ReturnCart:
    return _return_cart
