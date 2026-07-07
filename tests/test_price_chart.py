"""Графики динамики цен (этап 3.3, макет 5.6).

Геометрия графика — чистая функция build_price_chart, тестируемая без БД и веба:
ступенчатые кривые, единый масштаб Y, вырожденные случаи (одна точка, неизменная цена,
пустой ряд). Отдельно — чтение ряда price_history из БД и HTTP-фрагмент.
"""

import datetime as _dt
from decimal import Decimal

from sqlmodel import Session

from app.models.price_history import PriceHistory
from app.models.product import ProductStatus, UnitEnum
from app.schemas.product import ProductCreate, ProductEdit
from app.services.product_service import (
    build_price_chart,
    create_product,
    price_history,
    update_product,
)

WIDTH, HEIGHT, PAD = 640, 260, 40


def _data(**overrides) -> ProductCreate:
    defaults = dict(
        name="Тест товар",
        price_buy="50.00",
        price_sell="100.00",
        quantity="5",
        unit=UnitEnum.piece,
    )
    defaults.update(overrides)
    return ProductCreate(**defaults)


def _pt(day: int, buy: int, sell: int) -> tuple[_dt.datetime, int, int]:
    return (_dt.datetime(2026, 1, day), buy, sell)


# ── Чтение ряда price_history ────────────────────────────────────────────────

def test_price_history_chronological(db: Session):
    p = create_product(_data(price_buy="50.00", price_sell="100.00"), db)
    # Создание пишет первую точку. Правка цены пишет вторую (0.2 п.11 / 3.1).
    update_product(
        db, p.id,
        ProductEdit(name=p.name, price_buy="60.00", price_sell="120.00",
                    unit=UnitEnum.piece, status=ProductStatus.active, min_stock="0",
                    extra_info=None, supplier_names=[]),
    )
    points = price_history(db, p.id)
    assert len(points) == 2
    assert [pt.datetime for pt in points] == sorted(pt.datetime for pt in points)
    assert points[0].price_buy == 5000 and points[1].price_buy == 6000


# ── Ступенчатая кривая ───────────────────────────────────────────────────────

def test_step_line_two_points():
    """Для двух точек кривая идёт горизонталью, затем вертикальным скачком; последний
    сегмент дотянут до правого края."""
    chart = build_price_chart(
        [_pt(1, 1000, 2000), _pt(3, 1500, 2500)],
        width=WIDTH, height=HEIGHT, pad=PAD,
    )
    assert chart["has_data"] and not chart["single"]

    # Вершины кривой закупки: x0=pad (старая), x1=width-pad (новая точка).
    verts = [tuple(map(float, v.split(","))) for v in chart["buy_line"].split()]
    x0 = float(PAD)
    x1 = float(WIDTH - PAD)
    y0 = verts[0][1]  # y старой цены 1000
    # Промежуточная вершина (x1, y0) — горизонталь до момента изменения.
    assert (x1, y0) in verts
    # После неё — вертикаль к новой цене (x1, y1), y1 != y0.
    y1 = verts[-1][1]
    assert y1 != y0
    # Последняя вершина — на правом крае.
    assert verts[-1][0] == x1
    assert verts[0][0] == x0


def test_shared_y_scale():
    """Максимальная цена среди обеих кривых — на верхней границе, минимальная — на нижней."""
    chart = build_price_chart(
        [_pt(1, 1000, 3000), _pt(2, 1200, 2500)],
        width=WIDTH, height=HEIGHT, pad=PAD,
    )
    ys = [y for _, y in chart["buy_dots"] + chart["sell_dots"]]
    assert min(ys) == PAD              # максимальная цена (3000) → верх
    assert max(ys) == HEIGHT - PAD     # минимальная цена (1000) → низ
    # Подписи Y: макс и мин цены.
    prices = {price for _, price in chart["y_labels"]}
    assert prices == {1000, 3000}


def test_single_point_flat_full_width():
    """Одна точка → single, плоская линия во всю ширину, без деления на ноль по X."""
    chart = build_price_chart([_pt(1, 1000, 2000)], width=WIDTH, height=HEIGHT, pad=PAD)
    assert chart["single"] and chart["has_data"]
    xs = [float(v.split(",")[0]) for v in chart["buy_line"].split()]
    assert min(xs) == PAD and max(xs) == WIDTH - PAD
    # Обе точки линии на одной высоте (плоская).
    ys = {float(v.split(",")[1]) for v in chart["buy_line"].split()}
    assert len(ys) == 1


def test_unchanged_price_padded_center():
    """Цена одна на всём ряду (y_max == y_min) → диапазон паддится, линия по центру."""
    chart = build_price_chart(
        [_pt(1, 1000, 1000), _pt(5, 1000, 1000)],
        width=WIDTH, height=HEIGHT, pad=PAD,
    )
    assert not chart["single"]  # разные даты → не single, но цена постоянна
    center = PAD + (HEIGHT - 2 * PAD) / 2
    ys = [y for _, y in chart["buy_dots"]]
    assert all(y == round(center, 1) for y in ys)  # по центру, без деления на ноль
    assert len(chart["y_labels"]) == 1              # одна цена → одна подпись


def test_empty_series():
    chart = build_price_chart([], width=WIDTH, height=HEIGHT, pad=PAD)
    assert chart["has_data"] is False
    assert chart["buy_line"] == "" and chart["sell_line"] == ""
    assert chart["buy_dots"] == [] and chart["y_labels"] == []


# ── HTTP-слой ────────────────────────────────────────────────────────────────

def _create_via(engine, **overrides) -> int:
    with Session(engine) as s:
        return create_product(_data(**overrides), s).id


def test_http_prices_ok(client, test_engine):
    pid = _create_via(test_engine, name="Молоко")
    resp = client.get(f"/products/{pid}/prices")
    assert resp.status_code == 200
    assert "<svg" in resp.text
    assert "Динамика цен" in resp.text
    assert "Закупка" in resp.text and "Продажа" in resp.text


def test_http_prices_not_found(client):
    assert client.get("/products/999999/prices").status_code == 404


def test_http_prices_single_point_state(client, test_engine):
    # Новый товар без правок цены → одна точка price_history → «Цена не менялась».
    pid = _create_via(test_engine, name="Хлеб")
    resp = client.get(f"/products/{pid}/prices")
    assert resp.status_code == 200
    assert "Цена не менялась" in resp.text
