"""Документ завершения рабочего дня (этап 7.1, макет разд. 23).

Считает итоги смены из уже зафиксированных чеков продажи (1.3) и возврата (4.1/4.2); отбор
операций — строго по ``work_day_id`` (граница дня = смена ``WorkDay``, не окно по ``datetime``).
Себестоимость на момент операции реконструируется ``buy_price_asof`` (3.2) — снимок закупки в
строке чека не нужен. Закрытие смены и запись документа — одна транзакция (правило 2
CLAUDE.md): не бывает «смена закрыта, документа нет».

Денежная семантика (все суммы — int копейки):
- net_sales   = sales_total − returns_total  («итоговая сумма продаж за день»)
- net_profit  = net_sales − (cogs_sold − cogs_returned)  («сумма чистыми»)
  — деньги в кассе минус себестоимость товара, реально покинувшего склад (проданное−возврат).
"""

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlmodel import Session, select

from app.models.day_report import DayReport, DayReportLine, DayReportNumberCounter
from app.models.product import UnitEnum
from app.models.receipt import PaymentMethod, Receipt, ReceiptLine
from app.models.return_receipt import ReturnReceipt, ReturnReceiptLine
from app.models.work_day import WorkDay, WorkDayStatus
from app.services.money import line_total
from app.services.product_service import buy_price_asof
from app.services.work_day_service import get_open_day


@dataclass
class ComputedLine:
    """Строка потоварной детализации (до записи в БД)."""

    product_id: int
    name: str
    unit: UnitEnum
    quantity_sold: Decimal
    sum_sold: int
    cogs: int
    profit: int


@dataclass
class ComputedDay:
    """Итоги смены (до записи в БД). Поля повторяют ``DayReport`` без служебных."""

    sales_total: int = 0
    returns_total: int = 0
    net_sales: int = 0
    cogs_sold: int = 0
    cogs_returned: int = 0
    net_profit: int = 0
    rounding_total: int = 0
    sales_count: int = 0
    returns_count: int = 0
    cash_sales: int = 0
    card_sales: int = 0
    cash_returns: int = 0
    card_returns: int = 0
    lines: list[ComputedLine] = field(default_factory=list)


def compute_day(session: Session, work_day: WorkDay) -> ComputedDay:
    """Чистая агрегация итогов смены по ``work_day_id``. В БД не пишет (тестируется отдельно)."""
    receipts = session.exec(
        select(Receipt).where(Receipt.work_day_id == work_day.id)
    ).all()
    returns = session.exec(
        select(ReturnReceipt).where(ReturnReceipt.work_day_id == work_day.id)
    ).all()

    result = ComputedDay()

    # Продажи: суммы шапки + потоварный агрегат + себестоимость строк на момент чека.
    aggregate: dict[int, dict] = {}
    for receipt in receipts:
        result.sales_total += receipt.total
        result.rounding_total += receipt.rounding
        result.sales_count += 1
        if receipt.payment_method == PaymentMethod.cash:
            result.cash_sales += receipt.total
        else:
            result.card_sales += receipt.total

        lines = session.exec(
            select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id)
        ).all()
        for line in lines:
            buy = buy_price_asof(session, line.product_id, receipt.datetime)
            cogs = line_total(buy, line.quantity)
            result.cogs_sold += cogs

            entry = aggregate.get(line.product_id)
            if entry is None:
                entry = {
                    "name": line.name,
                    "unit": line.unit,
                    "quantity": Decimal("0"),
                    "sum_sold": 0,
                    "cogs": 0,
                }
                aggregate[line.product_id] = entry
            entry["quantity"] += line.quantity
            entry["sum_sold"] += line.total
            entry["cogs"] += cogs

    # Возвраты: суммы шапки + себестоимость возвращённого (товар вернулся на склад).
    for ret in returns:
        result.returns_total += ret.total
        result.returns_count += 1
        if ret.payment_method == PaymentMethod.cash:
            result.cash_returns += ret.total
        else:
            result.card_returns += ret.total

        lines = session.exec(
            select(ReturnReceiptLine).where(
                ReturnReceiptLine.return_receipt_id == ret.id
            )
        ).all()
        for line in lines:
            buy = buy_price_asof(session, line.product_id, ret.datetime)
            result.cogs_returned += line_total(buy, line.quantity)

    result.net_sales = result.sales_total - result.returns_total
    result.net_profit = result.net_sales - (result.cogs_sold - result.cogs_returned)

    result.lines = [
        ComputedLine(
            product_id=pid,
            name=entry["name"],
            unit=entry["unit"],
            quantity_sold=entry["quantity"],
            sum_sold=entry["sum_sold"],
            cogs=entry["cogs"],
            profit=entry["sum_sold"] - entry["cogs"],
        )
        for pid, entry in aggregate.items()
    ]
    # Сверху — товары, давшие больше выручки.
    result.lines.sort(key=lambda line: line.sum_sold, reverse=True)
    return result


def close_day_and_report(session: Session) -> DayReport:
    """Закрыть текущую смену и сформировать документ дня — одна транзакция (правило 2).

    Нет открытой смены → ``ValueError`` до любых мутаций. Смена без продаж/возвратов
    закрывается штатно и даёт нулевой документ.
    """
    day = get_open_day(session)
    if day is None:
        raise ValueError("Нет открытого рабочего дня")

    now = _dt.datetime.now()
    day.status = WorkDayStatus.closed
    day.closed_at = now
    session.add(day)

    computed = compute_day(session, day)

    # Номер документа — с блокировкой строки счётчика (как в complete_sale).
    counter = session.exec(
        select(DayReportNumberCounter)
        .where(DayReportNumberCounter.id == 1)
        .with_for_update()
    ).one()
    counter.last_value += 1
    session.add(counter)

    report = DayReport(
        report_number=counter.last_value,
        work_day_id=day.id,
        opened_at=day.opened_at,
        closed_at=day.closed_at,
        sales_total=computed.sales_total,
        returns_total=computed.returns_total,
        net_sales=computed.net_sales,
        cogs_sold=computed.cogs_sold,
        cogs_returned=computed.cogs_returned,
        net_profit=computed.net_profit,
        rounding_total=computed.rounding_total,
        sales_count=computed.sales_count,
        returns_count=computed.returns_count,
        cash_sales=computed.cash_sales,
        card_sales=computed.card_sales,
        cash_returns=computed.cash_returns,
        card_returns=computed.card_returns,
    )
    session.add(report)
    session.flush()  # получить report.id до вставки строк

    for cl in computed.lines:
        session.add(
            DayReportLine(
                day_report_id=report.id,
                product_id=cl.product_id,
                name=cl.name,
                unit=cl.unit,
                quantity_sold=cl.quantity_sold,
                sum_sold=cl.sum_sold,
                cogs=cl.cogs,
                profit=cl.profit,
            )
        )

    session.commit()
    session.refresh(report)
    return report


def list_day_reports(session: Session) -> list[DayReport]:
    """Сохранённые документы дня, от новых к старым (секция «Чеки за день»)."""
    return session.exec(
        select(DayReport).order_by(DayReport.closed_at.desc(), DayReport.id.desc())
    ).all()


def get_day_report(session: Session, report_id: int) -> DayReport | None:
    """Документ дня по id (просмотр сохранённого, разд. 23.5)."""
    return session.get(DayReport, report_id)


def day_report_lines(session: Session, report_id: int) -> list[DayReportLine]:
    """Потоварные строки документа, по убыванию суммы продаж (как при формировании)."""
    return session.exec(
        select(DayReportLine)
        .where(DayReportLine.day_report_id == report_id)
        .order_by(DayReportLine.sum_sold.desc(), DayReportLine.id.asc())
    ).all()
