"""Power table of a printer model: which kWh per hour a material needs (issue #174).

The lookup is for the calculation page (#161). It never guesses and never returns 0 for an
unknown material: it tells whether there was a match, no match (with the materials of the table
to choose from) or no table at all.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, PrinterModelPower

D = Decimal
ZERO = Decimal("0")


def same_material(name: str) -> str:
    """The form of a material name that is compared (no case, no surrounding blanks)."""
    return name.strip().casefold()


@dataclass(frozen=True)
class PowerLookup:
    status: Literal["found", "no_match", "no_table"]
    kwh_per_hour: Decimal | None = None
    # The materials of the table, sorted, to choose from when there was no exact match
    options: tuple[str, ...] = ()


def lookup(table: Mapping[str, Decimal] | None, material: str) -> PowerLookup:
    if not table:
        return PowerLookup("no_table")
    wanted = same_material(material)
    for name, value in table.items():
        if same_material(name) == wanted:
            return PowerLookup("found", value)
    return PowerLookup("no_match", options=tuple(sorted(table)))


def plate_kwh(parts: Iterable[tuple[Decimal, Decimal]]) -> Decimal:
    """kWh of a plate: the sum over the materials of hours x kWh per hour."""
    return sum((hours * kwh for hours, kwh in parts), ZERO)


def power_table(db: Session, device_id: int) -> dict[str, Decimal] | None:
    """The power table of the printer's model; None without a model or without rows."""
    device = db.get(Device, device_id)
    if device is None or device.printer_model_id is None:
        return None
    rows = db.scalars(
        select(PrinterModelPower).where(PrinterModelPower.model_id == device.printer_model_id)
    )
    return {row.material: row.kwh_per_hour for row in rows} or None
