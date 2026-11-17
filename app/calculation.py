"""Calculation core (issue #159): plan section 6, steps 1-9, as pure arithmetic.

No page and no database; the caller passes plain values. Everything is `Decimal`.
Grams / 1000, minutes / 60 and percent / 100 are converted explicitly. Nothing is rounded
except the suggested selling price (always upwards); pages round only when they show a value.
Empty optional fields (`None`) count as 0.
"""

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

from app.models import Rounding

ZERO = Decimal(0)
CENT = Decimal("0.01")
HUNDRED = Decimal(100)
THOUSAND = Decimal(1000)
SIXTY = Decimal(60)
NINETY_CENTS = Decimal("0.90")


class CalculationError(ValueError):
    """An input value makes no sense (negative, or no piece per plate)."""


@dataclass(frozen=True)
class MaterialLine:
    """One filament / colour of the plate."""

    grams: Decimal
    price_per_kg: Decimal


@dataclass(frozen=True)
class PowerLine:
    """The time one material of the plate needs and its power use (kWh per hour)."""

    minutes: Decimal
    kwh_per_hour: Decimal


@dataclass(frozen=True)
class CalculationInput:
    materials: list[MaterialLine]
    print_minutes: Decimal
    pieces_per_plate: int
    kwh_per_hour: Decimal | None = None
    # Power per material with its own time share (issue #161); added to `kwh_per_hour` x time
    power_lines: list[PowerLine] = field(default_factory=list)
    power_price: Decimal | None = None
    machine_rate: Decimal | None = None  # printer hourly rate + drying surcharge
    scrap_percent: Decimal | None = None
    setup_minutes: Decimal | None = None  # per plate
    rework_minutes: Decimal | None = None  # per piece
    labour_rate: Decimal | None = None
    margin_percent: Decimal | None = None
    rounding: Rounding = Rounding.NONE
    target_price: Decimal | None = None


@dataclass(frozen=True)
class CalculationResult:
    material_per_plate: Decimal
    power_per_plate: Decimal
    machine_per_plate: Decimal
    print_per_plate: Decimal
    print_per_piece: Decimal
    print_per_piece_with_scrap: Decimal
    labour_hours: Decimal
    labour_cost: Decimal
    cost_without_labour: Decimal
    cost_with_labour: Decimal
    suggested_price: Decimal
    selling_price: Decimal
    profit: Decimal
    hourly_wage: Decimal | None = field(default=None)  # None = no labour time


def _value(number: Decimal | None) -> Decimal:
    return ZERO if number is None else number


def round_price(value: Decimal, rounding: Rounding) -> Decimal:
    """The price rounded upwards: to the cent, 0.10, 0.50, the next x.90, or whole euros."""
    if rounding == Rounding.TEN_CENTS:
        return (value / Decimal("0.1")).to_integral_value(ROUND_CEILING) * Decimal("0.10")
    if rounding == Rounding.FIFTY_CENTS:
        return (value / Decimal("0.5")).to_integral_value(ROUND_CEILING) * Decimal("0.50")
    if rounding == Rounding.WHOLE_EURO:
        return value.to_integral_value(ROUND_CEILING).quantize(CENT)
    if rounding == Rounding.ENDS_90:
        # Smallest whole euro + 0.90 that is not lower than the value
        euros = (value - NINETY_CENTS).to_integral_value(ROUND_CEILING)
        return euros + NINETY_CENTS
    return value.quantize(CENT, rounding=ROUND_CEILING)


def _check(inp: CalculationInput) -> None:
    if inp.pieces_per_plate < 1:
        raise CalculationError("pieces per plate must be at least 1")
    numbers = [
        inp.print_minutes,
        inp.kwh_per_hour,
        inp.power_price,
        inp.machine_rate,
        inp.scrap_percent,
        inp.setup_minutes,
        inp.rework_minutes,
        inp.labour_rate,
        inp.margin_percent,
        inp.target_price,
    ]
    for line in inp.materials:
        numbers += [line.grams, line.price_per_kg]
    for power_line in inp.power_lines:
        numbers += [power_line.minutes, power_line.kwh_per_hour]
    if any(number is not None and number < 0 for number in numbers):
        raise CalculationError("values must not be negative")


def calculate(inp: CalculationInput) -> CalculationResult:
    """Steps 1-9 of plan section 6. Raises CalculationError for senseless input."""
    _check(inp)
    pieces = Decimal(inp.pieces_per_plate)
    hours = inp.print_minutes / SIXTY

    # 1-3: print cost
    material = sum((m.grams / THOUSAND * m.price_per_kg for m in inp.materials), ZERO)
    kwh = hours * _value(inp.kwh_per_hour)
    kwh += sum((p.minutes / SIXTY * p.kwh_per_hour for p in inp.power_lines), ZERO)
    power = kwh * _value(inp.power_price)
    machine = hours * _value(inp.machine_rate)
    per_plate = material + power + machine
    per_piece = per_plate / pieces
    with_scrap = per_piece * (1 + _value(inp.scrap_percent) / HUNDRED)

    # 4: labour (minutes x rate / 60 keeps whole numbers exact)
    labour_minutes = _value(inp.setup_minutes) / pieces + _value(inp.rework_minutes)
    labour_hours = labour_minutes / SIXTY
    labour_cost = labour_minutes * _value(inp.labour_rate) / SIXTY

    # 5-6: cost per piece
    without_labour = with_scrap
    with_labour = with_scrap + labour_cost

    # 7-9: price, profit, hourly wage
    suggested = round_price(with_labour * (1 + _value(inp.margin_percent) / HUNDRED), inp.rounding)
    selling = suggested if inp.target_price is None else inp.target_price
    profit = selling - with_labour
    wage = None
    if labour_minutes > 0:
        wage = (selling - without_labour) * SIXTY / labour_minutes

    return CalculationResult(
        material_per_plate=material,
        power_per_plate=power,
        machine_per_plate=machine,
        print_per_plate=per_plate,
        print_per_piece=per_piece,
        print_per_piece_with_scrap=with_scrap,
        labour_hours=labour_hours,
        labour_cost=labour_cost,
        cost_without_labour=without_labour,
        cost_with_labour=with_labour,
        suggested_price=suggested,
        selling_price=selling,
        profit=profit,
        hourly_wage=wage,
    )
