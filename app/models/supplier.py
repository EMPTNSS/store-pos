from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class SupplierStatus(str, Enum):
    active = "активный"
    archived = "архивный"


class Supplier(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str  # как ввёл продавец, для показа
    # нормализованный ключ уникальности: " ".join(name.split()).casefold()
    # (нормализацию делает сервис при вставке; здесь — колонка и ограничение)
    name_key: str = Field(unique=True, index=True)
    status: SupplierStatus = Field(default=SupplierStatus.active)


class ProductSupplierLink(SQLModel, table=True):
    """Связь товар↔поставщик (многие-ко-многим). Составной PK запрещает дубли пары.

    Без порядка/приоритета/«основного». «Без поставщика» = отсутствие строк.
    """

    __tablename__ = "product_supplier_link"

    product_id: int = Field(foreign_key="product.id", primary_key=True)
    supplier_id: int = Field(foreign_key="supplier.id", primary_key=True)
