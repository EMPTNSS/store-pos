import datetime as _dt
from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from app.models.counter import ProductCodeCounter
from app.models.movement import Movement, OperationType
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus
from app.models.supplier import ProductSupplierLink, Supplier
from app.schemas.product import ProductCreate, ProductEdit
from app.services.supplier_service import resolve_suppliers


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

    # 5. Разрешить поставщиков (реюз/инлайн-создание) и связать с товаром.
    #    Всё в этой же транзакции — атомарность создания товара сохраняется.
    for supplier in resolve_suppliers(data.supplier_names, session):
        session.add(
            ProductSupplierLink(product_id=product.id, supplier_id=supplier.id)
        )

    session.commit()
    session.refresh(product)
    return product


def get_product(session: Session, product_id: int) -> Optional[Product]:
    """Товар по id для карточки. None → роут отдаёт 404."""
    return session.get(Product, product_id)


def product_suppliers(session: Session, product_id: int) -> list[Supplier]:
    """Поставщики товара по алфавиту (через product_supplier_link)."""
    suppliers = session.exec(
        select(Supplier)
        .join(ProductSupplierLink, ProductSupplierLink.supplier_id == Supplier.id)
        .where(ProductSupplierLink.product_id == product_id)
    ).all()
    return sorted(suppliers, key=lambda s: s.name.casefold())


def product_view(session: Session, product: Product) -> dict:
    """Вычисляемые значения для показа карточки (спец 0.2 п.7). Нигде не хранятся.

    Наценка: разница в деньгах (копейки) и в процентах от закупки. При нулевой закупке
    процент не определён (деление на ноль) → None, в карточке показывается «—».
    """
    buy = product.price_buy
    sell = product.price_sell
    margin_pct: Optional[Decimal] = None
    if buy != 0:
        margin_pct = (Decimal(sell - buy) / Decimal(buy) * 100).quantize(Decimal("0.1"))
    return {
        "in_stock": product.quantity_current > 0,
        "margin_abs": sell - buy,
        "margin_pct": margin_pct,
        "suppliers": product_suppliers(session, product.id),
    }


def update_product(session: Session, product_id: int, data: ProductEdit) -> Product:
    """Правка паспорта товара (макет 5.5). Одна транзакция.

    Пишет точку price_history только если цена реально изменилась (0.2 п.11). Связи
    поставщиков заменяются целиком (удалить старые → вставить разрешённые). Коды/артикул/
    created_at/quantity_current не трогаются. Количество меняется отдельно (adjust_quantity).
    """
    now = _dt.datetime.now()
    product = session.get(Product, product_id)

    price_changed = (
        product.price_buy != data.price_buy or product.price_sell != data.price_sell
    )

    product.name = data.name
    product.price_buy = data.price_buy
    product.price_sell = data.price_sell
    product.unit = data.unit
    product.min_stock = data.min_stock
    product.status = data.status
    product.extra_info = data.extra_info
    session.add(product)

    if price_changed:
        session.add(
            PriceHistory(
                product_id=product.id,
                datetime=now,
                price_buy=data.price_buy,
                price_sell=data.price_sell,
            )
        )

    # Заменить связи поставщиков: удалить прежние, затем вставить разрешённые.
    # flush между удалением и вставкой — чтобы повторно выбранный поставщик не упёрся
    # в составной первичный ключ (product_id, supplier_id) ещё не удалённой строки.
    existing_links = session.exec(
        select(ProductSupplierLink).where(
            ProductSupplierLink.product_id == product.id
        )
    ).all()
    for link in existing_links:
        session.delete(link)
    session.flush()
    for supplier in resolve_suppliers(data.supplier_names, session):
        session.add(
            ProductSupplierLink(product_id=product.id, supplier_id=supplier.id)
        )

    session.commit()
    session.refresh(product)
    return product


def adjust_quantity(
    session: Session, product_id: int, new_quantity: Decimal
) -> Product:
    """Точечная корректировка остатка из карточки (макет 5.5, складской путь).

    Пишет движение «инвентаризация» на разницу с текущим остатком и обновляет
    quantity_current — одной транзакцией (CLAUDE.md правила 2, 5). Движение создаётся
    только на реальное изменение: при нулевой разнице ничего не пишется.
    """
    now = _dt.datetime.now()
    product = session.get(Product, product_id)

    delta = new_quantity - product.quantity_current
    if delta != 0:
        session.add(
            Movement(
                product_id=product.id,
                datetime=now,
                quantity=delta,  # знак: недостача −, излишек +
                operation_type=OperationType.inventory,
            )
        )
        product.quantity_current = new_quantity
        session.add(product)

    session.commit()
    session.refresh(product)
    return product
