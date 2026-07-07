"""Валидация входа для возврата (этап 4.1, дополнено 4.2). Проверка на границе (правило 3)."""

from decimal import Decimal

from pydantic import BaseModel, field_validator

from app.models.receipt import PaymentMethod
from app.schemas.product import Kopecks, PositiveDecimal


class ReturnComplete(BaseModel):
    """Проведение возврата: способ возврата денег обязан быть из перечня (правило 3).

    Из формы приходит английский код (`cash`/`card`) — имя члена ``PaymentMethod``
    (то же соглашение, что в ``SaleComplete``).
    """

    payment_method: PaymentMethod

    @field_validator("payment_method", mode="before")
    @classmethod
    def _coerce(cls, v: object) -> object:
        # Имя члена enum (cash/card) → сам член; значение (наличные/…) обрабатывает pydantic.
        if isinstance(v, str) and v in PaymentMethod.__members__:
            return PaymentMethod[v]
        return v


class ReturnLinePrice(BaseModel):
    """Правка цены возврата строки. Цена парсится в копейки (₽ → int), должна быть ≥ 0."""

    price: Kopecks

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Цена не может быть отрицательной")
        return v


class ReceiptLookup(BaseModel):
    """Поиск завершённого чека по номеру (4.2). Номер — целое больше 0."""

    number: int

    @field_validator("number")
    @classmethod
    def number_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Номер чека должен быть больше 0")
        return v


class ReturnFromReceipt(BaseModel):
    """Перенос строки чека в возврат (4.2). Количество > 0 (правило как в ``CartQuantity``).

    Верхнюю границу (≤ доступного) и существование строки проверяет роут/сервис — это
    состояние БД, а не поле формы.
    """

    source_line_id: int
    quantity: PositiveDecimal

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("Количество должно быть больше 0")
        return v
