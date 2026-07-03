from pydantic import BaseModel, field_validator

from app.models.receipt import PaymentMethod


class SaleComplete(BaseModel):
    """Валидация завершения продажи: способ оплаты обязан быть из перечня (правило 3).

    Из формы приходит английский код (`cash`/`card`) — имя члена ``PaymentMethod``.
    Английские коды в UI устойчивее кириллических значений к кодированию у клиента.
    """

    payment_method: PaymentMethod
    # Галочка «Распечатать накладную» (макет 1.4). Накладная — опциональна.
    print_invoice: bool = False

    @field_validator("payment_method", mode="before")
    @classmethod
    def _coerce(cls, v: object) -> object:
        # Имя члена enum (cash/card) → сам член; значение (наличные/…) обрабатывает pydantic.
        if isinstance(v, str) and v in PaymentMethod.__members__:
            return PaymentMethod[v]
        return v
