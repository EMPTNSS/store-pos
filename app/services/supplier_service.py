"""Поставщики: нормализация имени, список для выбора, разрешение при создании товара
(макет разд. 11.9).

Поставщик — минимальная сущность (только название). Инлайн-создание: имя, введённое в
форме, разрешается по нормализованному ключу — существующий переиспользуется, новый
создаётся. Нормализация в Python через ``casefold`` (тот же приём, что в
``product_search``): регистро- и пробело-независимо, не привязано к диалекту БД.
"""

from sqlmodel import Session, select

from app.models.supplier import Supplier, SupplierStatus


def supplier_key(name: str) -> str:
    """Нормализованный ключ уникальности имени: схлопнуть пробелы + casefold.

    «Кола», « кола », «КОЛА» → один ключ «кола». Пишется в ``Supplier.name_key``.
    """
    return " ".join(name.split()).casefold()


def list_active_suppliers(session: Session) -> list[Supplier]:
    """Активные поставщики по алфавиту — для выпадающего списка (datalist).

    Архивные скрыты из выбора; существующие связи с ними не трогаются.
    """
    suppliers = session.exec(
        select(Supplier).where(Supplier.status == SupplierStatus.active)
    ).all()
    return sorted(suppliers, key=lambda s: s.name.casefold())


def resolve_suppliers(names: list[str], session: Session) -> list[Supplier]:
    """Разрешить список введённых имён в объекты Supplier (реюз или инлайн-создание).

    Дедуп по нормализованному ключу (первое написание сохраняется для показа). Для
    каждого уникального ключа: найти существующего по ``name_key`` → переиспользовать,
    иначе создать нового (active) и ``flush`` (чтобы получить id для связи).

    НЕ коммитит: коммит принадлежит вызывающей транзакции (создание товара атомарно).
    Пустые имена сюда не попадают — они отсекаются валидатором схемы.
    """
    resolved: list[Supplier] = []
    seen_keys: set[str] = set()

    for raw in names:
        key = supplier_key(raw)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)

        supplier = session.exec(
            select(Supplier).where(Supplier.name_key == key)
        ).first()
        if supplier is None:
            supplier = Supplier(
                name=" ".join(raw.split()),
                name_key=key,
                status=SupplierStatus.active,
            )
            session.add(supplier)
            session.flush()  # получить supplier.id до вставки связи

        resolved.append(supplier)

    return resolved
