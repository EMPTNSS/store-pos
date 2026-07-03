"""Состояние экрана покупателя (этап 2.3).

Экран покупателя — пассивное зеркало кассы: во время продажи показывает живую
корзину (синглтон ``get_cart()``), после оплаты — «Спасибо за покупку», затем
экран ожидания. Здесь живут только маркер последней завершённой продажи и чистый
резолвер состояния экрана; ни БД, ни склад не трогаются.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from app.models.receipt import Receipt
from app.services.cart import CartView

DisplayKind = Literal["sale", "thanks", "idle"]


@dataclass
class LastSale:
    """Снимок только что завершённой продажи (для экрана благодарности)."""

    number: int
    total: int  # копейки
    at: datetime


@dataclass
class DisplayState:
    """Что показывать на экране покупателя прямо сейчас."""

    kind: DisplayKind
    cart: Optional[CartView] = None
    sale: Optional[LastSale] = None


# Маркер последней завершённой продажи — синглтон в памяти процесса (по образцу
# корзины). Нужен, чтобы показать благодарность после того, как корзина очищена.
_last_sale: Optional[LastSale] = None


def get_last_sale() -> Optional[LastSale]:
    return _last_sale


def mark_sale_completed(receipt: Receipt) -> None:
    """Запомнить только что завершённую продажу (вызывается из кассы после commit)."""
    global _last_sale
    _last_sale = LastSale(
        number=receipt.receipt_number,
        total=receipt.total,
        at=datetime.now(),
    )


def reset_last_sale() -> None:
    """Сбросить маркер (для тестов и чистого старта)."""
    global _last_sale
    _last_sale = None


def resolve_display_state(
    cart: CartView,
    last_sale: Optional[LastSale],
    now: datetime,
    thanks_seconds: int,
) -> DisplayState:
    """Что показать на экране покупателя. Чистая функция: без БД, веба и системных часов.

    Приоритет — продажа: пока в чеке есть строки, показываем чек (даже если недавно
    была продажа — начался новый покупатель). По пустому чеку показываем благодарность
    ещё ``thanks_seconds`` секунд после продажи, затем — экран ожидания. Маркер чистить
    при добавлении товара не нужно: непустой чек его перекрывает, пустой — гасит по времени.
    """
    if cart.lines:
        return DisplayState(kind="sale", cart=cart)
    if last_sale is not None and (now - last_sale.at).total_seconds() < thanks_seconds:
        return DisplayState(kind="thanks", sale=last_sale)
    return DisplayState(kind="idle")
