import datetime as _dt
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.types import quantity_column


class OperationType(str, Enum):
    income = "приход"
    sale = "продажа"
    return_ = "возврат"
    inventory = "инвентаризация"


class Movement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id")
    datetime: _dt.datetime
    quantity: Decimal = Field(sa_type=quantity_column())
    operation_type: OperationType
