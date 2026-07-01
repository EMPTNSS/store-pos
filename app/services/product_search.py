"""Поиск товаров для чека (разд. 3 макета).

Регистронезависимый подстрочный поиск по названию + префиксное совпадение по числовому
коду/артикулу. Фильтрация выполняется в Python (``str.casefold``), а не через SQLite
``LIKE``/``lower``: они регистронезависимы только для ASCII, а каталог содержит кириллицу
(«Молоко» ≠ «молоко» через ``LIKE``). Так логика не привязана к диалекту БД (CLAUDE.md).

Для одной точки с небольшим каталогом чтение всех товаров и отбор в памяти корректны и
портируемы. Точка будущей переделки: при росте каталога/переезде на Postgres поиск
переносится на сторону БД (индекс/ILIKE/полнотекстовый).
"""

from sqlmodel import Session, select

from app.models.product import Product


def search_products(session: Session, query: str, limit: int = 20) -> list[Product]:
    """Товары, подходящие под запрос продавца.

    Порядок: сначала совпадения по коду/артикулу (префикс), затем по названию (подстрока,
    по алфавиту). Пустой запрос → пустой список. Товары показываются независимо от статуса
    (архивные тоже находятся); сортировка по статусу отложена до этапа 8.
    """
    q = query.strip()
    if not q:
        return []

    q_folded = q.casefold()
    products = session.exec(select(Product)).all()

    code_matches: list[Product] = []
    name_matches: list[Product] = []
    for product in products:
        if product.numeric_code.startswith(q) or (
            product.article and product.article.casefold().startswith(q_folded)
        ):
            code_matches.append(product)
        elif q_folded in product.name.casefold():
            name_matches.append(product)

    name_matches.sort(key=lambda p: p.name.casefold())
    return (code_matches + name_matches)[:limit]
