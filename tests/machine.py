"""Helpers for tests that need printers with a machine rate (issue #162). All data is invented."""

from datetime import date, datetime
from decimal import Decimal

from app.models import Device
from tests.test_stock import db

YEARS = Decimal(5)
HOURS_PER_YEAR = Decimal(1000)


def add_printer(
    rate: str,
    bambuddy_printer_id: int | None = 1,
    name: str = "Testdrucker",
    active: bool = True,
) -> int:
    """A printer with the hourly rate `rate` (price / (5 years x 1000 h), no maintenance)."""
    with db() as session, session.begin():
        device = Device(
            kind="printer",
            name=name,
            bought_on=date(2026, 1, 1),
            price=Decimal(rate) * YEARS * HOURS_PER_YEAR,
            years=YEARS,
            hours_per_year=HOURS_PER_YEAR,
            maintenance_pct=Decimal(0),
            bambuddy_printer_id=bambuddy_printer_id,
            active=active,
            created_at=datetime(2026, 1, 1),
        )
        session.add(device)
        session.flush()
        return device.id


def add_dryer(yearly_cost: str, name: str = "Testtrockner") -> int:
    """A dryer that costs `yearly_cost` per year (price / 5 years, no maintenance)."""
    with db() as session, session.begin():
        device = Device(
            kind="dryer",
            name=name,
            bought_on=date(2026, 1, 1),
            price=Decimal(yearly_cost) * YEARS,
            years=YEARS,
            maintenance_pct=Decimal(0),
            active=True,
            created_at=datetime(2026, 1, 1),
        )
        session.add(device)
        session.flush()
        return device.id
