"""Оформление возврата — складской путь + документ «чек возврата» (этап 4.1, макет разд. 14).

Сохранение чека возврата + возврат остатка + запись движений `возврат` — одна транзакция БД
(правило 2 CLAUDE.md): либо всё целиком, либо ничего. Зеркалит ``complete_sale`` (1.3) с
противоположным знаком остатка/движения (товар возвращается на склад, разд. 14.3 п. 4).

4.2 (разд. 15.2–15.3): возврат может быть привязан к чеку-первоисточнику. Тогда пишутся
``source_receipt_id``/``source_line_id`` и проверяется инвариант перевозврата — нельзя
вернуть больше проданного по строке чека (с учётом ранее оформленных возвратов).
"""

import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal

from sqlmodel import Session, select

from app.models.movement import Movement, OperationType
from app.models.product import Product
from app.models.receipt import PaymentMethod, Receipt, ReceiptLine
from app.models.return_receipt import (
    ReturnNumberCounter,
    ReturnReceipt,
    ReturnReceiptLine,
)
from app.services.return_cart import ReturnCart


def already_returned(session: Session, source_line_id: int) -> Decimal:
    """Сколько уже возвращено по строке чека — Σ количеств всех возвратов с этим source_line_id."""
    lines = session.exec(
        select(ReturnReceiptLine).where(
            ReturnReceiptLine.source_line_id == source_line_id
        )
    ).all()
    return sum((line.quantity for line in lines), Decimal("0"))


@dataclass
class ReturnableLine:
    """Строка чека для экрана «возврат по чеку»: сколько продано, возвращено, доступно."""

    line: ReceiptLine
    sold: Decimal
    returned: Decimal
    available: Decimal


def returnable_lines(session: Session, receipt: Receipt) -> list[ReturnableLine]:
    """Строки чека с остатком, доступным к возврату (= продано − уже возвращено)."""
    lines = session.exec(
        select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id)
    ).all()
    result: list[ReturnableLine] = []
    for rl in lines:
        returned = already_returned(session, rl.id)
        result.append(
            ReturnableLine(
                line=rl,
                sold=rl.quantity,
                returned=returned,
                available=rl.quantity - returned,
            )
        )
    return result


def complete_return(
    session: Session, cart: ReturnCart, payment_method: PaymentMethod
) -> ReturnReceipt:
    """Оформить возврат текущего черновика одной транзакцией.

    Возвращает сохранённый ``ReturnReceipt``. Пустой возврат провести нельзя → ``ValueError``.
    Корзина очищается только после успешного commit (при ошибке возврат не теряется).
    Для корректирующего возврата (4.2) проверяется инвариант перевозврата.
    """
    view = cart.view()
    if not view.lines:
        raise ValueError("Возврат пуст")

    # Инвариант перевозврата (4.2): по каждой привязанной строке чека Σ возвращённого +
    # текущий возврат не превышает проданного. Проверяем до любых вставок в БД.
    for line in view.lines:
        if line.source_line_id is not None:
            receipt_line = session.get(ReceiptLine, line.source_line_id)
            if receipt_line is None:
                raise ValueError("Строка чека-первоисточника не найдена")
            if already_returned(session, line.source_line_id) + line.quantity > receipt_line.quantity:
                raise ValueError("Нельзя вернуть больше, чем продано")

    now = _dt.datetime.now()

    # 1. Получить следующий номер возврата с блокировкой строки счётчика.
    counter = session.exec(
        select(ReturnNumberCounter)
        .where(ReturnNumberCounter.id == 1)
        .with_for_update()
    ).one()
    counter.last_value += 1
    session.add(counter)

    # 2. Шапка чека возврата — итог зафиксирован из корзины, без округления вверх.
    #    source_receipt_id привязывает возврат к чеку-первоисточнику (4.2), NULL — свободный.
    receipt = ReturnReceipt(
        return_number=counter.last_value,
        datetime=now,
        payment_method=payment_method,
        total=view.total,
        source_receipt_id=cart.source_receipt_id,
    )
    session.add(receipt)
    session.flush()  # получить receipt.id до вставки строк

    # 3. Строки + возврат остатка + движение на каждую позицию.
    for line in view.lines:
        session.add(
            ReturnReceiptLine(
                return_receipt_id=receipt.id,
                product_id=line.product_id,
                name=line.name,
                unit=line.unit,
                price=line.price,
                quantity=line.quantity,
                total=line.total,
                source_line_id=line.source_line_id,  # привязка к строке чека (4.2), NULL — свободная
            )
        )

        product = session.get(Product, line.product_id)
        # Товар возвращается в остаток (разд. 14.3 п. 4).
        product.quantity_current += line.quantity

        session.add(
            Movement(
                product_id=line.product_id,
                datetime=now,
                quantity=line.quantity,  # возврат — положительная дельта остатка
                operation_type=OperationType.return_,
            )
        )

    session.commit()
    session.refresh(receipt)

    # Только после успешного commit очищаем черновик возврата.
    cart.clear()
    return receipt
