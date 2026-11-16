"""Rates and residual value of devices (plan, section 6). Pure calculation, no database.

All amounts are `Decimal`. Nothing here rounds: the pages round only when they show a value.
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
DAYS_PER_YEAR = Decimal("365")


def _with_maintenance(value: Decimal, maintenance_pct: Decimal) -> Decimal:
    return value * (1 + maintenance_pct / HUNDRED)


def printer_rate(
    price: Decimal, years: Decimal, hours_per_year: Decimal, maintenance_pct: Decimal
) -> Decimal:
    """Euro per print hour: price / (years x print hours per year) x (1 + maintenance / 100)."""
    return _with_maintenance(price / (years * hours_per_year), maintenance_pct)


def dryer_yearly_cost(price: Decimal, years: Decimal, maintenance_pct: Decimal) -> Decimal:
    """Euro per year: price / years x (1 + maintenance / 100). Electricity is not included."""
    return _with_maintenance(price / years, maintenance_pct)


def drying_surcharge(
    dryer_yearly_costs: Iterable[Decimal], printer_hours_per_year: Iterable[Decimal]
) -> Decimal:
    """Euro per print hour: the yearly costs of all active dryers spread over the print hours
    of all active printers. Zero when there is no dryer or no print hour."""
    costs = sum(dryer_yearly_costs, ZERO)
    hours = sum(printer_hours_per_year, ZERO)
    if costs == 0 or hours == 0:
        return ZERO
    return costs / hours


def residual_value(price: Decimal, years: Decimal, bought_on: date, today: date) -> Decimal:
    """What the device is still worth: it loses value evenly by the calendar day.

    Between 0 and the price; for a purchase date in the future it is the full price.
    """
    age = max((today - bought_on).days, 0)
    share = Decimal(age) / (DAYS_PER_YEAR * years)
    return max(price * (1 - share), ZERO)
