import datetime as _dt
from typing import Optional

from sqlmodel import Field, SQLModel


class PriceHistory(SQLModel, table=True):
    __tablename__ = "price_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id")
    datetime: _dt.datetime
    price_buy: int
    price_sell: int
