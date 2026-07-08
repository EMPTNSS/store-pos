"""Схемы заявок на пополнение (этап 5.3, макет разд. 11).

Валидация на границе (правило CLAUDE.md 3): нужное количество > 0. Приёмы переиспользуются
из ``schemas/product.py`` (``PositiveDecimal``). Выбор поставщика при пробросе разрешается в
роуте/сервисе через ``resolve_suppliers`` — отдельной схемы поставщика здесь нет.
"""

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator

from app.schemas.product import PositiveDecimal


class OrderLineInput(BaseModel):
    """Ввод строки заявки: нужное количество (> 0) и необязательное примечание (11.5/11.6)."""

    needed_quantity: PositiveDecimal
    comment: Optional[str] = None

    @field_validator("needed_quantity")
    @classmethod
    def needed_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("Нужное количество должно быть больше 0")
        return v

    @field_validator("comment")
    @classmethod
    def clean_comment(cls, v: Optional[str]) -> Optional[str]:
        # Пустая строка/пробелы → None: «нет примечания» хранится как NULL.
        if v is None:
            return None
        return v.strip() or None
