import datetime as _dt
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.types import quantity_column


class UnitEnum(str, Enum):
    piece = "шт"
    kg = "кг"
    meter = "м"
    liter = "л"
    pack = "упак"
    pair = "пара"
    bottle = "бутылка"

class ProductStatus(str, Enum):
    active = "активный"
    archived = "архивный"


class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    article: Optional[str] = Field(default=None)
    numeric_code: str = Field(unique=True)
    qr_code: Optional[str] = Field(default=None, unique=True)
    price_sell: int
    price_buy: int
    unit: UnitEnum
    min_stock: Decimal = Field(default=Decimal("0"), sa_type=quantity_column())
    status: ProductStatus = Field(default=ProductStatus.active)
    quantity_current: Decimal = Field(default=Decimal("0"), sa_type=quantity_column())
    created_at: _dt.datetime = Field(default_factory=_dt.datetime.now)
