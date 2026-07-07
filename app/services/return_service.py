"""Оформление возврата — складской путь + документ «чек возврата» (этап 4.1, макет разд. 14).

Сохранение чека возврата + возврат остатка + запись движений `возврат` — одна транзакция БД
(правило 2 CLAUDE.md): либо всё целиком, либо ничего. Зеркалит ``complete_sale`` (1.3) с
противоположным знаком остатка/движения (товар возвращается на склад, разд. 14.3 п. 4).
"""

import datetime as _dt

from sqlmodel import Session, select

from app.models.movement import Movement, OperationType
from app.models.product import Product
from app.models.receipt import PaymentMethod
from app.models.return_receipt import (
    ReturnNumberCounter,
    ReturnReceipt,
    ReturnReceiptLine,
)
from app.services.return_cart import ReturnCart


def complete_return(
    session: Session, cart: ReturnCart, payment_method: PaymentMethod
) -> ReturnReceipt:
    """Оформить возврат текущего черновика одной транзакцией.

    Возвращает сохранённый ``ReturnReceipt``. Пустой возврат провести нельзя → ``ValueError``.
    Корзина очищается только после успешного commit (при ошибке возврат не теряется).
    """
    view = cart.view()
    if not view.lines:
        raise ValueError("Возврат пуст")

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
    receipt = ReturnReceipt(
        return_number=counter.last_value,
        datetime=now,
        payment_method=payment_method,
        total=view.total,
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
