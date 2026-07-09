from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, field_validator

from app.models.product import ProductStatus, UnitEnum


def _parse_kopecks(v: object) -> int:
    if isinstance(v, int):
        return v
    try:
        d = Decimal(str(v).strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return int(d * 100)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Невалидное значение цены: {v!r}") from exc


def _parse_decimal(v: object) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        raise ValueError("Значение не может быть пустым")
    try:
        return Decimal(str(v).strip())
    except InvalidOperation as exc:
        raise ValueError(f"Невалидное число: {v!r}") from exc


Kopecks = Annotated[int, BeforeValidator(_parse_kopecks)]
PositiveDecimal = Annotated[Decimal, BeforeValidator(_parse_decimal)]


class ProductCreate(BaseModel):
    name: str
    article: Optional[str] = None
    price_buy: Kopecks
    price_sell: Kopecks
    quantity: PositiveDecimal
    unit: UnitEnum
    min_stock: PositiveDecimal = Decimal("0")
    qr_code: Optional[str] = None
    # Имена поставщиков из формы (0, 1 или несколько). Дедуп/создание — в сервисе.
    supplier_names: list[str] = []

    @field_validator("supplier_names")
    @classmethod
    def clean_supplier_names(cls, v: list[str]) -> list[str]:
        # Обрезать пробелы и выбросить пустые строки (пустые ряды формы игнорируются).
        return [s.strip() for s in v if s and s.strip()]

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Название не может быть пустым")
        return v.strip()

    @field_validator("price_sell")
    @classmethod
    def price_sell_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Цена продажи должна быть больше 0")
        return v

    @field_validator("price_buy")
    @classmethod
    def price_buy_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Цена закупки не может быть отрицательной")
        return v

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("Количество должно быть больше 0")
        return v

    @field_validator("min_stock")
    @classmethod
    def min_stock_non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError("Минимальный остаток не может быть отрицательным")
        return v


class ProductEdit(BaseModel):
    """Правка паспорта товара в карточке (макет 5.5). Без количества и кодов.

    Количество меняется отдельным путём (движение «инвентаризация», см. QuantityAdjust);
    коды/артикул read-only (якорят историю). Валидаторы — те же правила, что у ProductCreate.
    """

    name: str
    price_buy: Kopecks
    price_sell: Kopecks
    unit: UnitEnum
    min_stock: PositiveDecimal = Decimal("0")
    status: ProductStatus
    supplier_names: list[str] = []
    extra_info: Optional[str] = None

    @field_validator("supplier_names")
    @classmethod
    def clean_supplier_names(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Название не может быть пустым")
        return v.strip()

    @field_validator("price_sell")
    @classmethod
    def price_sell_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Цена продажи должна быть больше 0")
        return v

    @field_validator("price_buy")
    @classmethod
    def price_buy_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Цена закупки не может быть отрицательной")
        return v

    @field_validator("min_stock")
    @classmethod
    def min_stock_non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError("Минимальный остаток не может быть отрицательным")
        return v

    @field_validator("extra_info")
    @classmethod
    def clean_extra_info(cls, v: Optional[str]) -> Optional[str]:
        # Пустая строка/пробелы → None: «нет доп. информации» хранится как NULL.
        if v is None:
            return None
        trimmed = v.strip()
        return trimmed or None


class ProductReceive(BaseModel):
    """Приход партии товара при ручном приёме накладной (макет 12.3, этап 6.1).

    Кол-во обязательно и > 0 (приход всегда прибавляет к остатку). Цены — по желанию:
    пустое поле формы → None → цена не меняется. При наличии — те же правила, что у
    ProductCreate: закупка ≥ 0, продажа > 0. None-парсинг цен вынесен в before-валидатор,
    чтобы пустое поле не упиралось в разбор копеек.
    """

    received_quantity: PositiveDecimal  # пришедшее кол-во, > 0
    price_buy: Optional[int] = None  # новая закупка (копейки) или None = не менять
    price_sell: Optional[int] = None  # новая продажа (копейки) или None = не менять

    @field_validator("price_buy", "price_sell", mode="before")
    @classmethod
    def optional_kopecks(cls, v: object) -> Optional[int]:
        # Пустое/непереданное поле → None (цена не трогается); иначе — разбор в копейки.
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return _parse_kopecks(v)

    @field_validator("received_quantity")
    @classmethod
    def received_quantity_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("Количество должно быть больше 0")
        return v

    @field_validator("price_buy")
    @classmethod
    def price_buy_non_negative(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("Цена закупки не может быть отрицательной")
        return v

    @field_validator("price_sell")
    @classmethod
    def price_sell_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("Цена продажи должна быть больше 0")
        return v


class QuantityAdjust(BaseModel):
    """Корректировка фактического остатка из карточки (макет 5.5, складской путь).

    Новое количество ≥ 0. Отрицательные остатки запрещены (правила отложены, этап 8).
    """

    quantity: PositiveDecimal

    @field_validator("quantity")
    @classmethod
    def quantity_non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError("Количество не может быть отрицательным")
        return v
