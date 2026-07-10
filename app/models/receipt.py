import datetime as _dt
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.product import UnitEnum
from app.models.types import quantity_column


class PaymentMethod(str, Enum):
    cash = "наличные"
    card = "безналичные"


class Receipt(SQLModel, table=True):
    """Шапка завершённого чека. Суммы зафиксированы на момент продажи и не пересчитываются."""

    id: Optional[int] = Field(default=None, primary_key=True)
    receipt_number: int = Field(unique=True)  # человекочитаемый номер (см. ReceiptNumberCounter)
    datetime: _dt.datetime
    payment_method: PaymentMethod
    subtotal: int  # точная сумма строк в копейках
    rounding: int  # надбавка округления итога до целой ₽ (копейки, ≥ 0)
    total: int     # итог к оплате в копейках (subtotal + rounding)
    # Смена, в которой пробит чек (основа под 7.1). Nullable: строки до этой миграции — NULL.
    work_day_id: Optional[int] = Field(default=None, foreign_key="work_day.id", index=True)


class ReceiptLine(SQLModel, table=True):
    """Строка чека — самодостаточный снимок товара на момент продажи."""

    __tablename__ = "receipt_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    receipt_id: int = Field(foreign_key="receipt.id")
    product_id: int = Field(foreign_key="product.id")
    name: str
    unit: UnitEnum
    price_sell: int  # снимок цены продажи в копейках
    quantity: Decimal = Field(sa_type=quantity_column())
    total: int  # зафиксированная сумма строки в копейках


class ReceiptNumberCounter(SQLModel, table=True):
    """Счётчик последовательных номеров чека (по образцу ProductCodeCounter)."""

    __tablename__ = "receipt_number_counter"

    id: Optional[int] = Field(default=None, primary_key=True)
    last_value: int = Field(default=0)
