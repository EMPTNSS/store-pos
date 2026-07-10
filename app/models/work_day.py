"""Рабочий день (смена) — жизненный цикл кассовой смены.

Основа под этап 7.1 (документ завершения дня, макет разд. 23): здесь только сущность и
инвариант «ровно одна открытая смена», без итогового отчёта. Касса не проводит продажу/
возврат без открытого дня — guard живёт в сервисах продажи/возврата, а привязка
``work_day_id`` пишется той же транзакцией, что и чек (правило 2 CLAUDE.md).

Инвариант единственного открытого дня держит сервисный слой (``work_day_service``), по
образцу единственной открытой заявки на поставщика в ``order_service`` — без constraint в БД.
"""

import datetime as _dt
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class WorkDayStatus(str, Enum):
    # Мужской род: «рабочий день открыт/закрыт». НЕ путать с OrderStatus («заявка
    # открыта/закрыта», женский род) — это разные перечни для разных сущностей.
    open = "открыт"
    closed = "закрыт"


class WorkDay(SQLModel, table=True):
    """Смена: открывается продавцом, закрывается в конце дня. Номер не нужен — хватает id."""

    __tablename__ = "work_day"

    id: Optional[int] = Field(default=None, primary_key=True)
    status: WorkDayStatus = Field(default=WorkDayStatus.open)
    # default_factory, НЕ default=datetime.now() — иначе время вычислится один раз при
    # импорте (грабля, чинили в 0.2). Ставится на момент открытия.
    opened_at: _dt.datetime = Field(default_factory=_dt.datetime.now)
    closed_at: Optional[_dt.datetime] = Field(default=None)  # проставляется при закрытии
