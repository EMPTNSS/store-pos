import datetime as _dt
from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from app.models.counter import ProductCodeCounter
from app.models.movement import Movement, OperationType
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus
from app.models.receipt import Receipt, ReceiptLine
from app.models.supplier import ProductSupplierLink, Supplier
from app.schemas.product import ProductCreate, ProductEdit
from app.services.money import KOPECKS_PER_UNIT, line_total
from app.services.supplier_service import resolve_suppliers


def create_product(data: ProductCreate, session: Session) -> Product:
    now = _dt.datetime.now()

    # 1. Получить и инкрементировать счётчик с блокировкой строки
    counter = session.exec(
        select(ProductCodeCounter)
        .where(ProductCodeCounter.id == 1)
        .with_for_update()
    ).one()
    counter.last_value += 1
    numeric_code = str(counter.last_value).zfill(6)
    session.add(counter)

    # 2. Создать товар
    product = Product(
        name=data.name,
        article=data.article,
        numeric_code=numeric_code,
        qr_code=data.qr_code,
        price_sell=data.price_sell,
        price_buy=data.price_buy,
        unit=data.unit,
        min_stock=data.min_stock,
        status=ProductStatus.active,
        quantity_current=data.quantity,
        created_at=now,
    )
    session.add(product)
    session.flush()  # получить product.id до вставки связанных записей

    # 3. Записать движение-приход
    movement = Movement(
        product_id=product.id,
        datetime=now,
        quantity=data.quantity,
        operation_type=OperationType.income,
    )
    session.add(movement)

    # 4. Записать первую точку истории цен
    price_history = PriceHistory(
        product_id=product.id,
        datetime=now,
        price_buy=data.price_buy,
        price_sell=data.price_sell,
    )
    session.add(price_history)

    # 5. Разрешить поставщиков (реюз/инлайн-создание) и связать с товаром.
    #    Всё в этой же транзакции — атомарность создания товара сохраняется.
    for supplier in resolve_suppliers(data.supplier_names, session):
        session.add(
            ProductSupplierLink(product_id=product.id, supplier_id=supplier.id)
        )

    session.commit()
    session.refresh(product)
    return product


def get_product(session: Session, product_id: int) -> Optional[Product]:
    """Товар по id для карточки. None → роут отдаёт 404."""
    return session.get(Product, product_id)


def product_suppliers(session: Session, product_id: int) -> list[Supplier]:
    """Поставщики товара по алфавиту (через product_supplier_link)."""
    suppliers = session.exec(
        select(Supplier)
        .join(ProductSupplierLink, ProductSupplierLink.supplier_id == Supplier.id)
        .where(ProductSupplierLink.product_id == product_id)
    ).all()
    return sorted(suppliers, key=lambda s: s.name.casefold())


def is_low_stock(product: Product) -> bool:
    """Достигнут ли минимальный остаток (макет разд. 10.4). Чистая функция, без session.

    Товар «ниже минимума», когда порог задан и остаток его достиг:
    ``min_stock > 0 and quantity_current <= min_stock``. Порог включителен: остаток, равный
    минимуму, уже считается достигнутым (разд. 10.4 п.3). ``min_stock == 0`` = «минимум не
    задан» → отслеживание выключено (ноль — отсутствие порога, а не «заказывать всегда»).
    Сравнение Decimal — работает одинаково для штучных и весовых товаров.
    """
    return product.min_stock > 0 and product.quantity_current <= product.min_stock


def product_view(session: Session, product: Product) -> dict:
    """Вычисляемые значения для показа карточки (спец 0.2 п.7). Нигде не хранятся.

    Наценка: разница в деньгах (копейки) и в процентах от закупки. При нулевой закупке
    процент не определён (деление на ноль) → None, в карточке показывается «—».
    """
    buy = product.price_buy
    sell = product.price_sell
    margin_pct: Optional[Decimal] = None
    if buy != 0:
        margin_pct = (Decimal(sell - buy) / Decimal(buy) * 100).quantize(Decimal("0.1"))
    return {
        "in_stock": product.quantity_current > 0,
        "low_stock": is_low_stock(product),  # достигнут минимум (разд. 10.5)
        "margin_abs": sell - buy,
        "margin_pct": margin_pct,
        "suppliers": product_suppliers(session, product.id),
    }


def update_product(session: Session, product_id: int, data: ProductEdit) -> Product:
    """Правка паспорта товара (макет 5.5). Одна транзакция.

    Пишет точку price_history только если цена реально изменилась (0.2 п.11). Связи
    поставщиков заменяются целиком (удалить старые → вставить разрешённые). Коды/артикул/
    created_at/quantity_current не трогаются. Количество меняется отдельно (adjust_quantity).
    """
    now = _dt.datetime.now()
    product = session.get(Product, product_id)

    price_changed = (
        product.price_buy != data.price_buy or product.price_sell != data.price_sell
    )

    product.name = data.name
    product.price_buy = data.price_buy
    product.price_sell = data.price_sell
    product.unit = data.unit
    product.min_stock = data.min_stock
    product.status = data.status
    product.extra_info = data.extra_info
    session.add(product)

    if price_changed:
        session.add(
            PriceHistory(
                product_id=product.id,
                datetime=now,
                price_buy=data.price_buy,
                price_sell=data.price_sell,
            )
        )

    # Заменить связи поставщиков: удалить прежние, затем вставить разрешённые.
    # flush между удалением и вставкой — чтобы повторно выбранный поставщик не упёрся
    # в составной первичный ключ (product_id, supplier_id) ещё не удалённой строки.
    existing_links = session.exec(
        select(ProductSupplierLink).where(
            ProductSupplierLink.product_id == product.id
        )
    ).all()
    for link in existing_links:
        session.delete(link)
    session.flush()
    for supplier in resolve_suppliers(data.supplier_names, session):
        session.add(
            ProductSupplierLink(product_id=product.id, supplier_id=supplier.id)
        )

    session.commit()
    session.refresh(product)
    return product


def adjust_quantity(
    session: Session, product_id: int, new_quantity: Decimal
) -> Product:
    """Точечная корректировка остатка из карточки (макет 5.5, складской путь).

    Пишет движение «инвентаризация» на разницу с текущим остатком и обновляет
    quantity_current — одной транзакцией (CLAUDE.md правила 2, 5). Движение создаётся
    только на реальное изменение: при нулевой разнице ничего не пишется.
    """
    now = _dt.datetime.now()
    product = session.get(Product, product_id)

    delta = new_quantity - product.quantity_current
    if delta != 0:
        session.add(
            Movement(
                product_id=product.id,
                datetime=now,
                quantity=delta,  # знак: недостача −, излишек +
                operation_type=OperationType.inventory,
            )
        )
        product.quantity_current = new_quantity
        session.add(product)

    session.commit()
    session.refresh(product)
    return product


def receive_product(
    session: Session,
    product_id: int,
    received_quantity: Decimal,
    price_buy: Optional[int] = None,
    price_sell: Optional[int] = None,
) -> Product:
    """Приход существующего товара при ручном приёме накладной (макет 12.3, этап 6.1).

    Приход **прибавляет** к остатку и пишет движение «приход» — в отличие от
    adjust_quantity (3.1), которая выставляет абсолютный остаток и пишет «инвентаризация».
    Кол-во > 0 гарантировано схемой ProductReceive, поэтому движение пишется всегда.

    Цены необязательны: None → цена не меняется. Если хотя бы одна цена реально изменилась
    против текущей — добавляется одна точка price_history с новой парой (0.2 п.11). Всё —
    одной транзакцией (CLAUDE.md правила 2, 5): остаток, движение и точка истории вместе.
    """
    now = _dt.datetime.now()
    product = session.get(Product, product_id)

    product.quantity_current += received_quantity
    session.add(
        Movement(
            product_id=product.id,
            datetime=now,
            quantity=received_quantity,  # знак +: приход прибавляет
            operation_type=OperationType.income,
        )
    )

    new_buy = price_buy if price_buy is not None else product.price_buy
    new_sell = price_sell if price_sell is not None else product.price_sell
    price_changed = new_buy != product.price_buy or new_sell != product.price_sell
    product.price_buy = new_buy
    product.price_sell = new_sell
    session.add(product)

    if price_changed:
        session.add(
            PriceHistory(
                product_id=product.id,
                datetime=now,
                price_buy=new_buy,
                price_sell=new_sell,
            )
        )

    session.commit()
    session.refresh(product)
    return product


# ── История движений и статистика (этап 3.2, макет 5.7/5.4) ──────────────────
# Только чтение: количество берём из movement, деньги (прибыль) — из receipt_line
# + price_history (себестоимость на момент продажи). Хранилище не меняется.


def product_movements(session: Session, product_id: int) -> list[Movement]:
    """Все движения товара, от новых к старым — окно истории (макет 5.7).

    Вторичная сортировка по id разводит движения одной секунды (например несколько
    строк одного чека), чтобы порядок был устойчивым.
    """
    return session.exec(
        select(Movement)
        .where(Movement.product_id == product_id)
        .order_by(Movement.datetime.desc(), Movement.id.desc())
    ).all()


def buy_price_asof(session: Session, product_id: int, at: _dt.datetime) -> int:
    """Закупочная цена (копейки), действовавшая на момент ``at``.

    Последняя точка price_history с ``datetime <= at``. Если точек нет (не должно —
    первая пишется при создании товара) → fallback на текущую price_buy.
    """
    point = session.exec(
        select(PriceHistory)
        .where(
            PriceHistory.product_id == product_id,
            PriceHistory.datetime <= at,
        )
        .order_by(PriceHistory.datetime.desc(), PriceHistory.id.desc())
    ).first()
    if point is not None:
        return point.price_buy
    product = session.get(Product, product_id)
    return product.price_buy if product is not None else 0


def product_stats(session: Session, product_id: int) -> dict:
    """Статистика товара (макет 5.4): продано всего, движение по датам, чистая прибыль.

    - ``sold_total`` — Σ |quantity| по движениям типа «продажа».
    - ``by_date`` — движения, сгруппированные по дате: income (Σ положительных дельт,
      «прибыло») и outgoing (Σ модулей отрицательных, «убыло»); от новых дат к старым.
    - ``net_profit`` — выручка (receipt_line.total) минус себестоимость по закупочной
      цене на момент каждой продажи (buy_price_asof), в копейках. Только по продажам;
      недостача/излишек деньгами не оцениваются.
    """
    movements = session.exec(
        select(Movement).where(Movement.product_id == product_id)
    ).all()

    sold_total = sum(
        (-m.quantity for m in movements if m.operation_type == OperationType.sale),
        Decimal("0"),
    )

    buckets: dict[_dt.date, dict] = {}
    for m in movements:
        day = m.datetime.date()
        bucket = buckets.setdefault(
            day, {"income": Decimal("0"), "outgoing": Decimal("0")}
        )
        if m.quantity >= 0:
            bucket["income"] += m.quantity
        else:
            bucket["outgoing"] += -m.quantity
    by_date = [
        {"date": day, "income": b["income"], "outgoing": b["outgoing"]}
        for day, b in sorted(buckets.items(), reverse=True)
    ]

    # Чистая прибыль: выручка − себестоимость as-of по строкам чеков товара.
    rows = session.exec(
        select(ReceiptLine.quantity, ReceiptLine.total, Receipt.datetime)
        .join(Receipt, ReceiptLine.receipt_id == Receipt.id)
        .where(ReceiptLine.product_id == product_id)
    ).all()
    net_profit = 0
    for quantity, total, sold_at in rows:
        cost = line_total(buy_price_asof(session, product_id, sold_at), quantity)
        net_profit += total - cost

    return {"sold_total": sold_total, "by_date": by_date, "net_profit": net_profit}


# ── Динамика цен (этап 3.3, макет 5.6) ───────────────────────────────────────
# Только чтение price_history. Геометрия графика — чистая функция build_price_chart,
# тестируемая без БД и веба; шаблон отрисовывает готовые координаты SVG.


def price_history(session: Session, product_id: int) -> list[PriceHistory]:
    """Точки истории цен товара от старых к новым — ряд для графика (макет 5.6).

    Хронологический порядок нужен графику; вторичная сортировка по id разводит точки
    одной секунды (устойчивый порядок).
    """
    return session.exec(
        select(PriceHistory)
        .where(PriceHistory.product_id == product_id)
        .order_by(PriceHistory.datetime.asc(), PriceHistory.id.asc())
    ).all()


def _price_point(p) -> tuple[_dt.datetime, int, int]:
    """(datetime, price_buy, price_sell) из PriceHistory или готового кортежа."""
    if isinstance(p, tuple):
        return p
    return (p.datetime, p.price_buy, p.price_sell)


def build_price_chart(
    points, *, width: int = 640, height: int = 260, pad: int = 40
) -> dict:
    """Нормализация точек price_history в геометрию SVG (макет 5.6).

    Чистая функция без session — тестируется в изоляции. Ступенчатые кривые закупки и
    продажи на общих осях: X линейно по времени, Y линейно по цене (единый диапазон на
    обе кривые, чтобы зазор-наценка был правдив). Деньги остаются в копейках int; во
    float уходят только координаты пикселей SVG (это не деньги, округление некритично).

    Возвращает словарь для шаблона: has_data, single, buy_line/sell_line (строки points
    для polyline), buy_dots/sell_dots (маркеры), y_labels (цена в копейках) и x_labels
    (дата дд.мм.гггг).
    """
    empty = {
        "has_data": False, "single": False,
        "buy_line": "", "sell_line": "",
        "buy_dots": [], "sell_dots": [],
        "y_labels": [], "x_labels": [],
    }
    series = sorted((_price_point(p) for p in points), key=lambda t: t[0])
    if not series:
        return empty

    prices = [v for _, buy, sell in series for v in (buy, sell)]
    raw_min, raw_max = min(prices), max(prices)
    scale_min, scale_max = raw_min, raw_max
    if scale_max == scale_min:
        # Цена одна на всём ряду → раздвигаем диапазон, чтобы линия шла по центру,
        # а не делила на ноль (±10 %, но не меньше 1 ₽).
        delta = max(KOPECKS_PER_UNIT, raw_max // 10)
        scale_min, scale_max = raw_min - delta, raw_max + delta

    def r(v: float) -> float:
        return round(float(v), 1)

    def ycoord(price: int) -> float:
        # Больше цена — выше (меньше y): инверсия оси SVG.
        frac = (scale_max - price) / (scale_max - scale_min)
        return r(pad + frac * (height - 2 * pad))

    y_labels = [(ycoord(raw_max), raw_max)]
    if raw_max != raw_min:
        y_labels.append((ycoord(raw_min), raw_min))

    t_min, t_max = series[0][0], series[-1][0]

    if t_max == t_min:
        # Все точки одномоментны (одна точка / один день) → плоская линия во всю ширину.
        _, buy, sell = series[-1]
        yb, ys = ycoord(buy), ycoord(sell)
        left, right, mid = r(pad), r(width - pad), r(width / 2)
        return {
            "has_data": True, "single": True,
            "buy_line": f"{left},{yb} {right},{yb}",
            "sell_line": f"{left},{ys} {right},{ys}",
            "buy_dots": [(mid, yb)], "sell_dots": [(mid, ys)],
            "y_labels": y_labels,
            "x_labels": [(mid, t_max.strftime("%d.%m.%Y"))],
        }

    span = (t_max - t_min).total_seconds()

    def xcoord(t: _dt.datetime) -> float:
        frac = (t - t_min).total_seconds() / span
        return r(pad + frac * (width - 2 * pad))

    def step_line(index: int) -> tuple[str, list]:
        verts, dots, prev_y = [], [], None
        for t, buy, sell in series:
            x, y = xcoord(t), ycoord((buy, sell)[index])
            if prev_y is None:
                verts.append((x, y))
            else:
                verts.append((x, prev_y))  # горизонталь до момента изменения
                verts.append((x, y))       # вертикальный скачок цены
            dots.append((x, y))
            prev_y = y
        # Дотяжка последней цены до правого края (действует «до сих пор»).
        verts.append((r(width - pad), prev_y))
        return " ".join(f"{x},{y}" for x, y in verts), dots

    buy_line, buy_dots = step_line(0)
    sell_line, sell_dots = step_line(1)

    return {
        "has_data": True, "single": False,
        "buy_line": buy_line, "sell_line": sell_line,
        "buy_dots": buy_dots, "sell_dots": sell_dots,
        "y_labels": y_labels,
        "x_labels": [
            (r(pad), t_min.strftime("%d.%m.%Y")),
            (r(width - pad), t_max.strftime("%d.%m.%Y")),
        ],
    }
