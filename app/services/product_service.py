import datetime as _dt

from sqlmodel import Session, select

from app.models.counter import ProductCodeCounter
from app.models.movement import Movement, OperationType
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus
from app.schemas.product import ProductCreate


def create_product(data: ProductCreate, session: Session) -> Product:
    now = _dt.datetime.now()

    # 1. Получить и инкрементировать счётчик с блокировкой строки
    counter = session.exec(
        select(ProductCodeCounter)
        .where(ProductCodeCounter.id == 1)
        .with_for_update()
    ).one()
    counter.last_value += 1
    numeric_code = str(counter.last_value).zfill(6)
    session.add(counter)

    # 2. Создать товар
    product = Product(
        name=data.name,
        article=data.article,
        numeric_code=numeric_code,
        qr_code=data.qr_code,
        price_sell=data.price_sell,
        price_buy=data.price_buy,
        unit=data.unit,
        min_stock=data.min_stock,
        status=ProductStatus.active,
        quantity_current=data.quantity,
        created_at=now,
    )
    session.add(product)
    session.flush()  # получить product.id до вставки связанных записей

    # 3. Записать движение-приход
    movement = Movement(
        product_id=product.id,
        datetime=now,
        quantity=data.quantity,
        operation_type=OperationType.income,
    )
    session.add(movement)

    # 4. Записать первую точку истории цен
    price_history = PriceHistory(
        product_id=product.id,
        datetime=now,
        price_buy=data.price_buy,
        price_sell=data.price_sell,
    )
    session.add(price_history)

    session.commit()
    session.refresh(product)
    return product
