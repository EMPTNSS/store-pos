from typing import Optional

from sqlmodel import Field, SQLModel


class ProductCodeCounter(SQLModel, table=True):
    __tablename__ = "product_code_counter"

    id: Optional[int] = Field(default=None, primary_key=True)
    last_value: int = Field(default=0)
