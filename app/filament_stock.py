"""Stock and prices per filament (issue #238). Nothing here touches the database or Bambuddy.

The stock is the sum of the remaining weights of the active assigned spools, read live from
Bambuddy. The prices come from the values stored with the assignments, so archived and deleted
spools still count for them. A spool without a price is never counted as 0.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.bambuddy import Spool

ZERO = Decimal(0)
KG = Decimal(1000)


@dataclass(frozen=True)
class Held:
    """An assignment as far as the calculation needs it (values stored at the assignment)."""

    spool_id: int
    price_per_kg: Decimal | None
    weight_g: Decimal | None
    assigned_at: datetime


@dataclass(frozen=True)
class Summary:
    # None: not known because Bambuddy could not be read
    stock_kg: Decimal | None
    spool_count: int | None
    average_price: Decimal | None
    last_price: Decimal | None
    low: bool


def remaining_g(spool: Spool) -> Decimal:
    """Grams left on the spool; a spool of unknown weight adds nothing (never guessed)."""
    if spool.label_weight is None:
        return ZERO
    return max(spool.label_weight - (spool.weight_used or ZERO), ZERO)


def summarize(
    held: list[Held], live: dict[int, Spool] | None, min_stock_kg: Decimal | None
) -> Summary:
    """`live`: the spools of Bambuddy by number, or None when Bambuddy could not be read."""
    priced = [h for h in held if h.price_per_kg is not None]
    weighted = [h for h in priced if h.weight_g]
    average = None
    if weighted:
        grams = sum((h.weight_g for h in weighted), ZERO)
        average = sum((h.price_per_kg * h.weight_g for h in weighted), ZERO) / grams
    last = max(priced, key=lambda h: (h.assigned_at, h.spool_id)).price_per_kg if priced else None

    if live is None:
        return Summary(None, None, average, last, False)
    active = [
        live[h.spool_id] for h in held if h.spool_id in live and not live[h.spool_id].archived
    ]
    stock = sum((remaining_g(s) for s in active), ZERO) / KG
    low = min_stock_kg is not None and stock < min_stock_kg
    return Summary(stock, len(active), average, last, low)
