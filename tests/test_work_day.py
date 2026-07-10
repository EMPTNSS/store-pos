"""Тесты жизненного цикла рабочего дня (смены) и инварианта «одна открытая смена»."""

import pytest
from sqlmodel import select

from app.models.work_day import WorkDay, WorkDayStatus
from app.services.work_day_service import close_day, get_open_day, open_day


class TestOpenDay:
    def test_open_creates_open_day(self, db):
        day = open_day(db)
        assert day.id is not None
        assert day.status == WorkDayStatus.open
        assert day.opened_at is not None
        assert day.closed_at is None

    def test_get_open_day_finds_it(self, db):
        opened = open_day(db)
        found = get_open_day(db)
        assert found is not None
        assert found.id == opened.id

    def test_get_open_day_none_when_no_day(self, db):
        assert get_open_day(db) is None

    def test_open_when_already_open_rejected(self, db):
        open_day(db)
        with pytest.raises(ValueError):
            open_day(db)
        # Инвариант: ровно одна открытая смена — вторая не создалась.
        open_days = db.exec(
            select(WorkDay).where(WorkDay.status == WorkDayStatus.open)
        ).all()
        assert len(open_days) == 1


class TestCloseDay:
    def test_close_marks_closed_with_timestamp(self, db):
        opened = open_day(db)
        closed = close_day(db)
        assert closed.id == opened.id
        assert closed.status == WorkDayStatus.closed
        assert closed.closed_at is not None

    def test_get_open_day_none_after_close(self, db):
        open_day(db)
        close_day(db)
        assert get_open_day(db) is None

    def test_close_without_open_rejected(self, db):
        with pytest.raises(ValueError):
            close_day(db)

    def test_close_already_closed_rejected(self, db):
        open_day(db)
        close_day(db)
        # Открытой смены больше нет → повторное закрытие отклоняется.
        with pytest.raises(ValueError):
            close_day(db)

    def test_reopen_after_close_allowed(self, db):
        open_day(db)
        close_day(db)
        second = open_day(db)  # новая смена — растущая история, не переоткрытие старой
        assert second.status == WorkDayStatus.open
        assert get_open_day(db).id == second.id
