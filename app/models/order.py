"""Заявки на пополнение (этап 5.3, макет разд. 11).

Заявка — намерение заказать товар, а не движение склада. Создание/правка/закрытие заявки
НЕ меняют ``Product.quantity_current`` и НЕ пишут ``Movement`` (возврат в остаток при приезде
заказа — это приём накладной, этап 6). Отдельные таблицы (по образцу ``receipt``/
``return_receipt``), чтобы не трогать протестированные инварианты продажи/склада.

Строка заявки хранит только намерение (``needed_quantity`` + ``comment``). «Текущее
количество» и «минимальный остаток» (макет 11.5) не снимаются, а берутся из ``Product`` на
момент показа (решение заказчика, см. ТЗ 5.3 разд. 2).
"""

import datetime as _dt
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.types import quantity_column


class OrderStatus(str, Enum):
    open = "открыта"
    closed = "закрыта"


class Order(SQLModel, table=True):
    """Шапка заявки (макет 11.5). Одна открытая заявка на пару (поставщик, магазин)."""

    # 'order' — зарезервированное слово SQL; используем явное имя таблицы.
    __tablename__ = "purchase_order"

    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: int = Field(foreign_key="supplier.id")  # заявка всегда по поставщику (11.9.1)
    store: str  # магазин (11.4); из settings.store_name
    status: OrderStatus = Field(default=OrderStatus.open)
    created_at: _dt.datetime = Field(default_factory=_dt.datetime.now)  # дата создания (11.5)
    closed_at: Optional[_dt.datetime] = Field(default=None)  # проставляется при закрытии (11.7)


class OrderLine(SQLModel, table=True):
    """Строка заявки: товар + нужное количество + примечание (макет 11.5/11.6).

    В открытой заявке не более одной строки на товар (повторный проброс обновляет
    ``needed_quantity``) — инвариант держит сервис.
    """

    __tablename__ = "purchase_order_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="purchase_order.id")
    product_id: int = Field(foreign_key="product.id")
    needed_quantity: Decimal = Field(sa_type=quantity_column())  # нужное кол-во для заказа
    comment: Optional[str] = Field(default=None)  # примечание к позиции
