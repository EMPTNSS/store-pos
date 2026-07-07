"""Чек возврата — отдельный документ склада (этап 4.1, макет разд. 14).

Отдельные таблицы, чтобы не трогать продажный ``Receipt`` и его протестированные
инварианты (1.3). Строки — самодостаточный снимок товара на момент оформления
(правило 4 CLAUDE.md).

Этап 4.2 (разд. 15.2–15.3): возврат может быть **привязан к чеку-первоисточнику** —
``source_receipt_id`` в шапке и ``source_line_id`` в строке. Оба nullable: свободный
возврат 4.1 остаётся с ``NULL``. По ``source_line_id`` считается «уже возвращено» и
проверяется инвариант перевозврата (нельзя вернуть больше проданного).
"""

import datetime as _dt
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.product import UnitEnum
from app.models.receipt import PaymentMethod  # переиспользуем enum, не дублируем
from app.models.types import quantity_column


class ReturnReceipt(SQLModel, table=True):
    """Шапка чека возврата. Итог зафиксирован на момент оформления, не пересчитывается."""

    __tablename__ = "return_receipt"

    id: Optional[int] = Field(default=None, primary_key=True)
    return_number: int = Field(unique=True)  # человекочитаемый номер (см. ReturnNumberCounter)
    datetime: _dt.datetime
    payment_method: PaymentMethod  # способ возврата денег (пометка)
    total: int  # итог возврата в копейках = Σ строк (без округления вверх до ₽)
    # Чек-первоисточник (4.2). NULL — свободный возврат без привязки (4.1).
    source_receipt_id: Optional[int] = Field(default=None, foreign_key="receipt.id")


class ReturnReceiptLine(SQLModel, table=True):
    """Строка чека возврата — снимок товара на момент возврата."""

    __tablename__ = "return_receipt_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    return_receipt_id: int = Field(foreign_key="return_receipt.id")
    product_id: int = Field(foreign_key="product.id")
    name: str
    unit: UnitEnum
    price: int  # цена возврата за единицу в копейках (указана продавцом)
    quantity: Decimal = Field(sa_type=quantity_column())
    total: int  # зафиксированная сумма строки в копейках
    # Строка чека-первоисточника (4.2): по ней считается «уже возвращено» и
    # проверяется перевозврат. NULL — свободная строка возврата (4.1).
    source_line_id: Optional[int] = Field(default=None, foreign_key="receipt_line.id")


class ReturnNumberCounter(SQLModel, table=True):
    """Счётчик последовательных номеров чека возврата (по образцу ReceiptNumberCounter)."""

    __tablename__ = "return_number_counter"

    id: Optional[int] = Field(default=None, primary_key=True)
    last_value: int = Field(default=0)
