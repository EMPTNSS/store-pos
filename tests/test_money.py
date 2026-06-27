from decimal import Decimal

from app.services.money import line_total, round_total_up


def test_piece_integer_quantity():
    # 3 шт × 20.00 ₽ = 60.00 ₽
    assert line_total(2000, Decimal("3")) == 6000


def test_quantity_one():
    assert line_total(14850, Decimal("1")) == 14850


def test_weighted_quantity_rounds_half_up():
    # 1.5 кг × 33.33 ₽ = 49.995 ₽ → округление до 50.00 ₽ (ROUND_HALF_UP)
    assert line_total(3333, Decimal("1.5")) == 5000


def test_weighted_quantity_rounds_down():
    # 1.2 кг × 10.10 ₽ = 12.12 ₽ ровно
    assert line_total(1010, Decimal("1.2")) == 1212


def test_half_kopeck_rounds_up():
    # 0.5 коп округляется вверх
    assert line_total(1, Decimal("0.5")) == 1


def test_zero_price():
    assert line_total(0, Decimal("5")) == 0


def test_result_is_int():
    assert isinstance(line_total(1000, Decimal("2.5")), int)


# --- округление итога чека до целой ₽ --------------------------------------

def test_round_total_already_whole():
    # 40.00 ₽ — кратно целой ₽, не меняется
    assert round_total_up(4000) == 4000


def test_round_total_up_partial():
    # 86.50 ₽ → 87.00 ₽
    assert round_total_up(8650) == 8700


def test_round_total_up_one_kopeck():
    # 86.01 ₽ → 87.00 ₽ (вверх даже на копейку)
    assert round_total_up(8601) == 8700


def test_round_total_up_99_kopecks():
    # 86.99 ₽ → 87.00 ₽
    assert round_total_up(8699) == 8700


def test_round_total_zero():
    assert round_total_up(0) == 0
