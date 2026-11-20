"""Spools for which Bambuddy charged its default filament price (issue #228).

Bambuddy takes the default price per kg from its settings whenever a spool has no price. The
archive does not say so. The usage history does say which spool a print used and what it cost,
so the check is done on the result: the cost fits "grams x default price" but not
"grams x price of the spool". Nothing is changed, the caller only shows a notice.
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.bambuddy import Spool, Usage

CENT = Decimal("0.01")
# The usage line is written when the print ends; the archive keeps the time a little later.
SAME_PRINT_SECONDS = 60
# Costs are rounded to cents on both sides, so one cent of difference is still the same price.
TOLERANCE = Decimal("0.01")


def _cost(grams: Decimal, per_kg: Decimal) -> Decimal:
    return (grams * per_kg / 1000).quantize(CENT, rounding=ROUND_HALF_UP)


def _fits(cost: Decimal, grams: Decimal, per_kg: Decimal) -> bool:
    return abs(cost - _cost(grams, per_kg)) <= TOLERANCE


def standard_price_spools(
    finished_at: datetime | None,
    printer_id: int | None,
    usages: list[Usage],
    spools: dict[int, Spool],
    default_per_kg: Decimal | None,
) -> list[Spool]:
    """The spools of the print (found by end time and printer) that were charged at the default
    price. Lines without a known spool or cost, and prints without an end time, are skipped."""
    if finished_at is None or default_per_kg is None:
        return []
    result: list[Spool] = []
    for line in usages:
        spool = spools.get(line.spool_id) if line.spool_id is not None else None
        if spool is None or line.cost is None or line.created_at is None:
            continue
        if line.printer_id != printer_id:
            continue
        if abs((line.created_at - finished_at).total_seconds()) > SAME_PRINT_SECONDS:
            continue
        own = spool.cost_per_kg or Decimal(0)
        at_default = _fits(line.cost, line.grams, default_per_kg)
        if at_default and not _fits(line.cost, line.grams, own) and spool not in result:
            result.append(spool)
    return result
