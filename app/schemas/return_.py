"""Валидация входа для возврата (этап 4.1). Проверка на границе, до логики (правило 3)."""

from pydantic import BaseModel, field_validator

from app.models.receipt import PaymentMethod
from app.schemas.product import Kopecks


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
