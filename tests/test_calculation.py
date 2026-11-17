"""Calculation core (issue #159): pure arithmetic, no page, no database. All data is invented.
The test case numbers refer to the comment on the issue.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.calculation import (
    CalculationError,
    CalculationInput,
    MaterialLine,
    PowerLine,
    calculate,
    round_price,
)
from app.models import Rounding

D = Decimal

# 1 plate with 2 pieces: 100 g at 20 EUR/kg, 2 h, 0.15 kWh/h at 0.30 EUR/kWh, 0.50 EUR/h
BASE = CalculationInput(
    materials=[MaterialLine(grams=D(100), price_per_kg=D(20))],
    print_minutes=D(120),
    pieces_per_plate=2,
    kwh_per_hour=D("0.15"),
    power_price=D("0.30"),
    machine_rate=D("0.50"),
)


def test_1_plate_cost_adds_material_power_machine() -> None:
    result = calculate(BASE)
    assert result.material_per_plate == D("2.00")
    assert result.power_per_plate == D("0.09")
    assert result.machine_per_plate == D("1.00")
    assert result.print_per_plate == D("3.09")


def test_2_several_material_lines_are_added() -> None:
    lines = [MaterialLine(D(100), D(20)), MaterialLine(D(50), D(30))]
    result = calculate(replace(BASE, materials=lines))
    assert result.material_per_plate == D("3.50")


def test_3_print_cost_per_piece() -> None:
    assert calculate(BASE).print_per_piece == D("1.545")


def test_4_scrap_raises_print_cost() -> None:
    assert calculate(replace(BASE, scrap_percent=D(10))).print_per_piece_with_scrap == D("1.6995")
    assert calculate(BASE).print_per_piece_with_scrap == D("1.545")


def test_5_labour_from_setup_and_rework() -> None:
    result = calculate(replace(BASE, setup_minutes=D(10), rework_minutes=D(5), labour_rate=D(30)))
    assert result.labour_hours == D(1) / D(6)
    assert result.labour_cost == D("5.00")


def test_6_empty_labour_is_zero() -> None:
    result = calculate(replace(BASE, setup_minutes=None, rework_minutes=None, labour_rate=D(30)))
    assert result.labour_hours == 0
    assert result.labour_cost == 0


def test_7_cost_without_and_with_labour() -> None:
    result = calculate(
        replace(
            BASE, scrap_percent=D(10), setup_minutes=D(10), rework_minutes=D(5), labour_rate=D(30)
        )
    )
    assert result.cost_without_labour == D("1.6995")
    assert result.cost_with_labour == D("6.6995")


def test_8_suggestion_with_margin_and_rounding() -> None:
    inp = replace(
        BASE,
        scrap_percent=D(10),
        setup_minutes=D(10),
        rework_minutes=D(5),
        labour_rate=D(30),
        margin_percent=D(20),
        rounding=Rounding.FIFTY_CENTS,
    )
    result = calculate(inp)
    assert result.suggested_price == D("8.50")
    assert calculate(replace(inp, rounding=Rounding.NONE)).suggested_price == D("8.04")


@pytest.mark.parametrize(
    ("value", "rounding", "expected"),
    [
        ("12.31", Rounding.NONE, "12.31"),
        ("12.31", Rounding.TEN_CENTS, "12.40"),
        ("12.31", Rounding.FIFTY_CENTS, "12.50"),
        ("12.31", Rounding.ENDS_90, "12.90"),
        ("12.31", Rounding.WHOLE_EURO, "13.00"),
        ("12.95", Rounding.NONE, "12.95"),
        ("12.95", Rounding.TEN_CENTS, "13.00"),
        ("12.95", Rounding.FIFTY_CENTS, "13.00"),
        ("12.95", Rounding.ENDS_90, "13.90"),
        ("12.95", Rounding.WHOLE_EURO, "13.00"),
        ("12.90", Rounding.ENDS_90, "12.90"),
        ("12.001", Rounding.NONE, "12.01"),
        ("0", Rounding.ENDS_90, "0.90"),
    ],
)
def test_8_rounding_goes_upwards(value: str, rounding: Rounding, expected: str) -> None:
    assert round_price(D(value), rounding) == D(expected)


def test_9_target_price_wins_over_suggestion() -> None:
    inp = replace(BASE, margin_percent=D(50))
    without = calculate(inp)
    assert without.selling_price == without.suggested_price
    with_target = calculate(replace(inp, target_price=D("10.00")))
    assert with_target.selling_price == D("10.00")
    assert with_target.suggested_price == without.suggested_price


def test_10_profit_per_piece_can_be_negative() -> None:
    inp = replace(BASE, setup_minutes=D(10), rework_minutes=D(5), labour_rate=D(30))
    assert calculate(replace(inp, target_price=D("10.00"))).profit == D("10.00") - D("6.545")
    assert calculate(replace(inp, target_price=D("1.00"))).profit < 0


def test_11_effective_hourly_wage() -> None:
    inp = replace(
        BASE,
        scrap_percent=D(10),
        setup_minutes=D(10),
        rework_minutes=D(5),
        labour_rate=D(30),
        target_price=D("10.00"),
    )
    assert calculate(inp).hourly_wage == D("49.803")


def test_11_no_labour_time_means_no_hourly_wage() -> None:
    result = calculate(replace(BASE, target_price=D("10.00")))
    assert result.hourly_wage is None


def test_12_empty_optional_fields_count_as_zero() -> None:
    inp = CalculationInput(
        materials=[], print_minutes=D(60), pieces_per_plate=1, machine_rate=D("2.00")
    )
    result = calculate(inp)
    assert result.print_per_plate == D("2.00")
    assert result.power_per_plate == 0
    assert result.suggested_price == D("2.00")
    assert result.profit == 0


@pytest.mark.parametrize(
    "change",
    [
        {"pieces_per_plate": 0},
        {"pieces_per_plate": -1},
        {"print_minutes": D(-1)},
        {"materials": [MaterialLine(D(-1), D(20))]},
        {"materials": [MaterialLine(D(1), D(-20))]},
        {"machine_rate": D(-1)},
        {"scrap_percent": D(-1)},
        {"setup_minutes": D(-1)},
        {"target_price": D(-1)},
    ],
)
def test_13_invalid_values_are_rejected(change: dict) -> None:
    with pytest.raises(CalculationError):
        calculate(replace(BASE, **change))


def test_14_uses_decimal_without_float_errors() -> None:
    inp = replace(
        BASE, materials=[MaterialLine(D("0.1"), D("1000")), MaterialLine(D("0.2"), D("1000"))]
    )
    assert calculate(inp).material_per_plate == D("0.3")
    result = calculate(BASE)
    for value in (result.print_per_plate, result.cost_with_labour, result.suggested_price):
        assert isinstance(value, Decimal)


# --- Issue #161: power per material with its own time share (test cases 1-3) ---------------


def test_161_1_power_adds_the_time_share_of_every_material() -> None:
    # PLA 60 min at 0.10 kWh/h and PETG 30 min at 0.13 kWh/h, 0.30 EUR/kWh
    inp = replace(
        BASE,
        kwh_per_hour=None,
        power_lines=[PowerLine(D(60), D("0.10")), PowerLine(D(30), D("0.13"))],
    )
    result = calculate(inp)
    assert result.power_per_plate == D("0.03") + D("0.0195")


def test_161_2_without_power_lines_the_result_is_as_before() -> None:
    assert calculate(replace(BASE, power_lines=[])).power_per_plate == D("0.09")


@pytest.mark.parametrize("line", [PowerLine(D(-1), D("0.1")), PowerLine(D(10), D("-0.1"))])
def test_161_3_negative_power_values_are_refused(line: PowerLine) -> None:
    with pytest.raises(CalculationError):
        calculate(replace(BASE, power_lines=[line]))
