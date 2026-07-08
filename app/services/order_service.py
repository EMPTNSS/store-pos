"""Заявки на пополнение (этап 5.3, макет разд. 11).

Заявочная логика вне веб-обвязки (структура проекта): детект кандидатов, ручной проброс с
выбором поставщика, редактирование строк, закрытие и чтение для панели/архива.

Инварианты (см. ТЗ 5.3):
- Заявка НЕ трогает склад: ни ``Movement``, ни ``Product.quantity_current`` (намерение
  заказать ≠ приход; приход — этап 6).
- Одна ОТКРЫТАЯ заявка на пару (поставщик, магазин); проброс дописывает/обновляет строку.
- В открытой заявке не более одной строки на товар (повторный проброс обновляет количество).
- «Текущее количество» и «минимальный остаток» не хранятся — берутся из товара при показе.
"""

import datetime as _dt
from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from app.config import settings
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product
from app.models.supplier import Supplier
from app.services.product_service import is_low_stock, product_suppliers


# ── Детект кандидатов (макет 11.2) ───────────────────────────────────────────

def is_order_candidate(product: Product) -> bool:
    """Товар — кандидат на заказ, если достиг минимума ИЛИ закончился (макет 11.2).

    Чистая функция, без session. ``is_low_stock`` (5.2) закрывает «достиг минимума»;
    ``quantity_current <= 0`` — «закончился» (в т.ч. случай ``min_stock == 0``, который 5.2
    осознанно отдал 5.3). Проброс кандидата в заявку остаётся ручным (макет 11.9.5).
    """
    return is_low_stock(product) or product.quantity_current <= 0


def suggested_quantity(product: Product) -> Decimal:
    """Подсказка нужного количества: сколько не хватает до минимума (иначе 1).

    Продавец правит значение перед добавлением (макет 11.9.5 п.5); это лишь удобный дефолт.
    """
    gap = product.min_stock - product.quantity_current
    return gap if gap > Decimal("0") else Decimal("1")


def _products_in_open_orders(session: Session) -> set[int]:
    """id товаров, уже присутствующих в какой-либо открытой заявке — для пометки кандидатов."""
    rows = session.exec(
        select(OrderLine.product_id)
        .join(Order, OrderLine.order_id == Order.id)
        .where(Order.status == OrderStatus.open)
    ).all()
    return set(rows)


def list_candidates(session: Session) -> list[dict]:
    """Кандидаты на заказ, сгруппированные по поставщику (макет 11.9.4).

    Товар с несколькими поставщиками попадает в группу каждого. Товары без поставщика —
    в отдельную группу «Разный / без поставщика» (``supplier = None``, идёт последней).
    Каждый элемент: ``{"product", "already" (уже в открытой заявке), "suggested"}``.
    """
    in_open = _products_in_open_orders(session)
    candidates = [p for p in session.exec(select(Product)).all() if is_order_candidate(p)]

    groups: dict[int, dict] = {}
    none_group: dict = {"supplier": None, "items": []}
    for product in candidates:
        item = {
            "product": product,
            "already": product.id in in_open,
            "suggested": suggested_quantity(product),
        }
        suppliers = product_suppliers(session, product.id)
        if not suppliers:
            none_group["items"].append(item)
        else:
            for supplier in suppliers:
                group = groups.setdefault(
                    supplier.id, {"supplier": supplier, "items": []}
                )
                group["items"].append(item)

    result = [
        groups[k]
        for k in sorted(groups, key=lambda k: groups[k]["supplier"].name.casefold())
    ]
    if none_group["items"]:
        result.append(none_group)
    return result


# ── Проброс и наполнение заявки (макет 11.9.5, 11.6) ─────────────────────────

def _get_or_create_open_order(session: Session, supplier_id: int) -> Order:
    """Открытая заявка поставщика в текущем магазине; создать, если нет. Не коммитит."""
    order = session.exec(
        select(Order).where(
            Order.supplier_id == supplier_id,
            Order.store == settings.store_name,
            Order.status == OrderStatus.open,
        )
    ).first()
    if order is None:
        order = Order(
            supplier_id=supplier_id,
            store=settings.store_name,
            status=OrderStatus.open,
            created_at=_dt.datetime.now(),
        )
        session.add(order)
        session.flush()  # получить order.id до вставки строки
    return order


def _upsert_line(
    session: Session,
    order: Order,
    product_id: int,
    needed_quantity: Decimal,
    comment: Optional[str],
) -> OrderLine:
    """Добавить строку товара или обновить существующую (одна строка на товар). Не коммитит."""
    line = session.exec(
        select(OrderLine).where(
            OrderLine.order_id == order.id,
            OrderLine.product_id == product_id,
        )
    ).first()
    if line is None:
        line = OrderLine(
            order_id=order.id,
            product_id=product_id,
            needed_quantity=needed_quantity,
            comment=comment,
        )
    else:
        line.needed_quantity = needed_quantity
        if comment is not None:
            line.comment = comment
    session.add(line)
    return line


def push_to_order(
    session: Session,
    product_id: int,
    supplier_id: int,
    needed_quantity: Decimal,
    comment: Optional[str] = None,
) -> Order:
    """Проброс кандидата в заявку поставщика (макет 11.9.5). Одна транзакция.

    Найти/создать открытую заявку поставщика, добавить/обновить строку товара. Остаток и
    движение не трогаются.
    """
    order = _get_or_create_open_order(session, supplier_id)
    _upsert_line(session, order, product_id, needed_quantity, comment)
    session.commit()
    session.refresh(order)
    return order


def add_manual_line(
    session: Session,
    order_id: int,
    product_id: int,
    needed_quantity: Decimal = Decimal("1"),
    comment: Optional[str] = None,
) -> Order:
    """Ручное добавление произвольного товара в открытую заявку (макет 11.6)."""
    order = session.get(Order, order_id)
    if order is None:
        raise ValueError("Заявка не найдена")
    if order.status != OrderStatus.open:
        raise ValueError("Закрытая заявка не редактируется")
    _upsert_line(session, order, product_id, needed_quantity, comment)
    session.commit()
    session.refresh(order)
    return order


def _open_order_of_line(session: Session, line_id: int) -> tuple[OrderLine, Order]:
    """Строка + её заявка с проверкой, что заявка открыта (правки закрытой запрещены)."""
    line = session.get(OrderLine, line_id)
    if line is None:
        raise ValueError("Строка заявки не найдена")
    order = session.get(Order, line.order_id)
    if order is None or order.status != OrderStatus.open:
        raise ValueError("Закрытая заявка не редактируется")
    return line, order


def update_line(
    session: Session,
    line_id: int,
    needed_quantity: Decimal,
    comment: Optional[str],
) -> OrderLine:
    """Изменить нужное количество и примечание строки (макет 11.6)."""
    line, _ = _open_order_of_line(session, line_id)
    line.needed_quantity = needed_quantity
    line.comment = comment
    session.add(line)
    session.commit()
    session.refresh(line)
    return line


def remove_line(session: Session, line_id: int) -> None:
    """Удалить строку из открытой заявки (макет 11.6)."""
    line, _ = _open_order_of_line(session, line_id)
    session.delete(line)
    session.commit()


def close_order(session: Session, order_id: int) -> Order:
    """Закрыть заявку при заказе (макет 11.7). Склад не трогается; заявка сохраняется (11.8)."""
    order = session.get(Order, order_id)
    if order is None:
        raise ValueError("Заявка не найдена")
    if order.status == OrderStatus.closed:
        raise ValueError("Заявка уже закрыта")
    order.status = OrderStatus.closed
    order.closed_at = _dt.datetime.now()
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


# ── Чтение для панели и архива ───────────────────────────────────────────────

def order_view(session: Session, order: Order) -> dict:
    """Заявка + поставщик + строки с живыми кол-вом/минимумом товара (макет 11.5).

    «Текущее количество» и «минимальный остаток» берутся из ``Product`` (не снимок).
    """
    supplier = session.get(Supplier, order.supplier_id)
    lines = session.exec(
        select(OrderLine).where(OrderLine.order_id == order.id).order_by(OrderLine.id)
    ).all()
    line_views = []
    for line in lines:
        product = session.get(Product, line.product_id)
        line_views.append(
            {
                "line": line,
                "product": product,
                "current": product.quantity_current if product else Decimal("0"),
                "min_stock": product.min_stock if product else Decimal("0"),
            }
        )
    return {"order": order, "supplier": supplier, "lines": line_views}


def list_open_orders(session: Session) -> list[dict]:
    """Открытые заявки по алфавиту поставщика — для панели."""
    orders = session.exec(
        select(Order).where(Order.status == OrderStatus.open)
    ).all()
    views = [order_view(session, o) for o in orders]
    return sorted(
        views, key=lambda v: v["supplier"].name.casefold() if v["supplier"] else ""
    )


def list_closed_orders(session: Session) -> list[dict]:
    """Закрытые заявки (архив, 11.8) от новых к старым — только для чтения."""
    orders = session.exec(
        select(Order)
        .where(Order.status == OrderStatus.closed)
        .order_by(Order.closed_at.desc(), Order.id.desc())
    ).all()
    return [order_view(session, o) for o in orders]
