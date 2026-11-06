"""Денежные правила, наценка 0–100 %, FIFO (чистая логика без базы)."""

from decimal import Decimal

import pytest

from autoservice.domain.errors import InsufficientStock, ValidationError
from autoservice.domain.fifo import Lot, allocate_fifo, total_cost
from autoservice.domain.markup import MARKUP_PRESETS, price_from_cost, resolve_markup, validate_markup
from autoservice.domain.money import D, allocate, fmt_money, fmt_qty, money, proportional


def test_float_is_rejected():
    with pytest.raises(TypeError):
        money(0.1)


def test_round_half_up():
    assert money("0.005") == Decimal("0.01")
    assert money("2.675") == Decimal("2.68")  # с float было бы 2.67
    assert money("-0.005") == Decimal("-0.01")
    assert money("1 234,565") == Decimal("1234.57")


@pytest.mark.parametrize("cost,markup,price", [
    ("1000", "20", "1200.00"),
    ("1000", "50", "1500.00"),
    ("1000", "100", "2000.00"),
    ("1000", "0", "1000.00"),
    ("1000", "30", "1300.00"),
    ("333.33", "33.33", "444.43"),
    ("0.01", "50", "0.02"),  # 0,015 → 0,02 (половина вверх)
    ("799.99", "12.5", "899.99"),
])
def test_markup_formula(cost, markup, price):
    assert price_from_cost(Decimal(cost), Decimal(markup)) == Decimal(price)


def test_markup_presets_and_range():
    assert MARKUP_PRESETS == [Decimal(x) for x in (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)]
    assert validate_markup("0") == Decimal("0.00")
    assert validate_markup("100") == Decimal("100.00")
    assert validate_markup("37,25") == Decimal("37.25")
    for bad in ("-0.01", "100.01", "150", "12.345"):
        with pytest.raises(ValidationError):
            validate_markup(bad)


def test_markup_priority():
    assert resolve_markup(operation="10", batch="20", part="30", default="40").source == "operation"
    assert resolve_markup(batch="20", part="30", default="40").percent == Decimal("20")
    assert resolve_markup(part="30", default="40").source == "part"
    assert resolve_markup(default="40").source == "default"
    assert resolve_markup().percent is None


def test_allocate_keeps_every_kopeck():
    parts = allocate(Decimal("100.00"), [Decimal(1), Decimal(1), Decimal(1)])
    assert sum(parts) == Decimal("100.00")
    assert sorted(parts) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert allocate(Decimal("0.05"), [Decimal("3"), Decimal("1")]) == [Decimal("0.04"), Decimal("0.01")]


def test_proportional():
    assert proportional(Decimal("100"), Decimal("1"), Decimal("3")) == Decimal("33.33")


def test_fifo_partial_and_exhaust():
    lots = [Lot(1, D("10"), D("1000.00")), Lot(2, D("5"), D("600.00"))]
    portions = allocate_fifo(lots, D("12"))
    assert [(p.batch_id, p.qty, p.cost) for p in portions] == [(1, D("10"), D("1000.00")), (2, D("2"), D("240.00"))]
    assert total_cost(portions) == D("1240.00")


def test_fifo_no_kopeck_loss_on_repeated_partial():
    remaining_qty, remaining_cost = D("3"), D("100.00")
    total = D("0")
    for _ in range(3):
        p = allocate_fifo([Lot(1, remaining_qty, remaining_cost)], D("1"))[0]
        total += p.cost
        remaining_qty -= p.qty
        remaining_cost -= p.cost
    assert total == D("100.00") and remaining_cost == 0


def test_fifo_unknown_cost_and_insufficient():
    portions = allocate_fifo([Lot(1, D("2"), None)], D("1"))
    assert total_cost(portions) is None
    with pytest.raises(InsufficientStock):
        allocate_fifo([Lot(1, D("2"), D("10.00"))], D("3"))


def test_formatting():
    assert fmt_money(Decimal("4850")) == "4 850,00 ₽"
    assert fmt_qty(Decimal("4.000")) == "4"
    assert fmt_qty(Decimal("4.500")) == "4,5"
