"""Завершение продажи — ядро денежного и складского пути.

Сохранение чека + списание остатка + запись движений `продажа` — одна транзакция БД
(правило 2 CLAUDE.md): либо всё целиком, либо ничего. Суммы фиксируются из корзины как есть
и не пересчитываются (правило 4). Паттерн повторяет ``product_service.create_product``.
"""

import datetime as _dt

from sqlmodel import Session, select

from app.models.movement import Movement, OperationType
from app.models.product import Product
from app.models.receipt import (
    PaymentMethod,
    Receipt,
    ReceiptLine,
    ReceiptNumberCounter,
)
from app.services.cart import Cart
from app.services.work_day_service import get_open_day


def complete_sale(
    session: Session, cart: Cart, payment_method: PaymentMethod
) -> Receipt:
    """Завершить продажу текущего чека одной транзакцией.

    Возвращает сохранённый ``Receipt``. Пустой чек или отсутствие открытой смены →
    ``ValueError`` до любых мутаций (корзина цела). Корзина очищается только после
    успешного commit (при ошибке чек не теряется).
    """
    view = cart.view()
    if not view.lines:
        raise ValueError("Чек пуст")

    # Продажа возможна только в открытую смену (основа под 7.1). Проверяем до любых
    # мутаций БД и счётчика — корзина остаётся нетронутой (как при пустом чеке).
    day = get_open_day(session)
    if day is None:
        raise ValueError("Рабочий день не открыт")

    now = _dt.datetime.now()

    # 1. Получить следующий номер чека с блокировкой строки счётчика.
    counter = session.exec(
        select(ReceiptNumberCounter)
        .where(ReceiptNumberCounter.id == 1)
        .with_for_update()
    ).one()
    counter.last_value += 1
    session.add(counter)

    # 2. Шапка чека — суммы зафиксированы из корзины, не пересчитываются.
    receipt = Receipt(
        receipt_number=counter.last_value,
        datetime=now,
        payment_method=payment_method,
        subtotal=view.subtotal,
        rounding=view.rounding,
        total=view.grand_total,
        work_day_id=day.id,
    )
    session.add(receipt)
    session.flush()  # получить receipt.id до вставки строк

    # 3. Строки чека + списание остатка + движение на каждую позицию.
    for line in view.lines:
        session.add(
            ReceiptLine(
                receipt_id=receipt.id,
                product_id=line.product_id,
                name=line.name,
                unit=line.unit,
                price_sell=line.price_sell,
                quantity=line.quantity,
                total=line.total,
            )
        )

        product = session.get(Product, line.product_id)
        # Остаток может уйти в минус (правила отрицательных остатков отложены, разд. 17).
        product.quantity_current -= line.quantity

        session.add(
            Movement(
                product_id=line.product_id,
                datetime=now,
                quantity=-line.quantity,  # продажа — отрицательная дельта остатка
                operation_type=OperationType.sale,
            )
        )

    session.commit()
    session.refresh(receipt)

    # Только после успешного commit очищаем корзину кассы.
    cart.clear()
    return receipt
