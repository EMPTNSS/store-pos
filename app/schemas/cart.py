from decimal import Decimal

from pydantic import BaseModel, field_validator

from app.schemas.product import PositiveDecimal


class CartQuantity(BaseModel):
    """Валидация количества для строки чека.

    Дробное количество допускается (весовые товары, разд. 22). Полноценный UX ввода
    веса со сканера/поиска — в пункте 1.2.
    """

    quantity: PositiveDecimal

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("Количество должно быть больше 0")
        return v
