"""Документ завершения рабочего дня (этап 7.1, макет разд. 23).

Снимок итогов смены: суммы зафиксированы на момент закрытия и не пересчитываются (как
``Receipt``). Привязан к смене (``work_day_id`` unique — один документ на смену). Отдельные
таблицы, чтобы не трогать протестированные инварианты продажи/возврата/склада: 7.1 только
читает их данные и агрегирует.

Себестоимость («чистыми») не снимается в строке чека, а реконструируется из ``price_history``
(``buy_price_asof``, этап 3.2) при формировании документа — здесь хранится уже посчитанный
результат.
"""

import datetime as _dt
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.product import UnitEnum
from app.models.types import quantity_column


class DayReport(SQLModel, table=True):
    """Шапка документа дня (макет 23.3). Итоги смены, зафиксированы при закрытии."""

    __tablename__ = "day_report"

    id: Optional[int] = Field(default=None, primary_key=True)
    report_number: int = Field(unique=True)  # человекочитаемый номер (см. DayReportNumberCounter)
    work_day_id: int = Field(unique=True, foreign_key="work_day.id")  # один документ на смену
    opened_at: _dt.datetime  # снимок WorkDay.opened_at — начало смены
    closed_at: _dt.datetime  # снимок WorkDay.closed_at — момент закрытия
    # Все суммы — int в копейках (железные правила CLAUDE.md).
    sales_total: int    # Σ Receipt.total смены (валовые продажи, вкл. округление)
    returns_total: int  # Σ ReturnReceipt.total смены
    net_sales: int      # sales_total − returns_total («итоговая сумма продаж за день»)
    cogs_sold: int      # себестоимость проданного
    cogs_returned: int  # себестоимость возвращённого (вернулось на склад)
    net_profit: int     # net_sales − (cogs_sold − cogs_returned) («сумма чистыми»)
    rounding_total: int # Σ Receipt.rounding смены (доп. фин. инфо)
    sales_count: int    # число чеков продажи
    returns_count: int  # число чеков возврата
    cash_sales: int     # продажи наличными (Σ Receipt.total)
    card_sales: int     # продажи безналичными
    cash_returns: int   # возвраты наличными (Σ ReturnReceipt.total)
    card_returns: int   # возвраты безналичными


class DayReportLine(SQLModel, table=True):
    """Потоварная детализация продаж за смену (макет 23.3). Снимок агрегата по товару."""

    __tablename__ = "day_report_line"

    id: Optional[int] = Field(default=None, primary_key=True)
    day_report_id: int = Field(foreign_key="day_report.id")
    product_id: int = Field(foreign_key="product.id")  # ссылка (переход в карточку)
    name: str          # снимок названия
    unit: UnitEnum     # снимок единицы
    quantity_sold: Decimal = Field(sa_type=quantity_column())  # продано единиц за смену
    sum_sold: int      # сумма продаж по товару (Σ ReceiptLine.total)
    cogs: int          # себестоимость проданного по товару
    profit: int        # sum_sold − cogs — прибыль по товару


class DayReportNumberCounter(SQLModel, table=True):
    """Счётчик последовательных номеров документа дня (по образцу ReceiptNumberCounter)."""

    __tablename__ = "day_report_number_counter"

    id: Optional[int] = Field(default=None, primary_key=True)
    last_value: int = Field(default=0)
