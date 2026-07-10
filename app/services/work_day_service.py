"""Жизненный цикл рабочего дня (смены).

Инвариант «ровно одна открытая смена» держится здесь, на сервисе — без constraint в БД,
по образцу ``order_service._get_or_create_open_order`` (единственная открытая заявка на
поставщика). Проверка и изменение статуса — одна транзакция.

Итоговый документ завершения дня (макет 23.2–23.5) здесь НЕ формируется — это этап 7.1.
"""

import datetime as _dt
from typing import Optional

from sqlmodel import Session, select

from app.models.work_day import WorkDay, WorkDayStatus


def get_open_day(session: Session) -> Optional[WorkDay]:
    """Текущая открытая смена, если есть. Источник правды для guard кассы."""
    return session.exec(
        select(WorkDay).where(WorkDay.status == WorkDayStatus.open)
    ).first()


def open_day(session: Session) -> WorkDay:
    """Открыть новую смену. Если уже есть открытая — ``ValueError``. Одна транзакция."""
    if get_open_day(session) is not None:
        raise ValueError("Рабочий день уже открыт")
    day = WorkDay(status=WorkDayStatus.open)
    session.add(day)
    session.commit()
    session.refresh(day)
    return day


def close_day(session: Session) -> WorkDay:
    """Закрыть текущую открытую смену. Если открытой нет — ``ValueError``.

    Отчёт по дню (макет 23.2–23.5) не формируется — это этап 7.1.
    """
    day = get_open_day(session)
    if day is None:
        raise ValueError("Нет открытого рабочего дня")
    day.status = WorkDayStatus.closed
    day.closed_at = _dt.datetime.now()
    session.add(day)
    session.commit()
    session.refresh(day)
    return day
