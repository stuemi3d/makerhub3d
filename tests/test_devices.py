"""Devices and hourly rates (issue #157): the calculation, the form check and the migration."""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command

from app import device_form, devices
from app.db import alembic_config, make_engine, run_migrations

D = Decimal


# --- Hourly rate of a printer (cases 1-3) ------------------------------------------------------


def test_printer_rate_with_maintenance() -> None:
    rate = devices.printer_rate(D("749"), D("5"), D("1000"), D("35"))

    assert rate == D("0.20223")
    assert rate.quantize(D("0.0001")) == D("0.2022")
    assert rate.quantize(D("0.01")) == D("0.20")


def test_printer_rate_without_maintenance() -> None:
    assert devices.printer_rate(D("1000"), D("5"), D("1000"), D("0")) == D("0.2")


def test_every_printer_has_its_own_rate() -> None:
    first = devices.printer_rate(D("749"), D("5"), D("1000"), D("35"))
    second = devices.printer_rate(D("500"), D("5"), D("800"), D("0"))

    assert first != second
    assert second == D("0.125")


# --- Drying surcharge (cases 4-8) --------------------------------------------------------------


def test_dryer_costs_per_year() -> None:
    assert devices.dryer_yearly_cost(D("132.99"), D("5"), D("0")).quantize(D("0.01")) == D("26.60")


def test_dryer_maintenance_raises_the_yearly_costs() -> None:
    assert devices.dryer_yearly_cost(D("133"), D("5"), D("0")) == D("26.6")
    assert devices.dryer_yearly_cost(D("133"), D("5"), D("10")) == D("29.26")


def test_surcharge_with_one_dryer_and_one_printer() -> None:
    yearly = devices.dryer_yearly_cost(D("132.99"), D("5"), D("0"))

    surcharge = devices.drying_surcharge([yearly], [D("1000")])

    assert surcharge.quantize(D("0.001")) == D("0.027")


def test_surcharge_with_two_dryers_and_two_printers() -> None:
    costs = [D("26.60"), D("40.00")]

    surcharge = devices.drying_surcharge(costs, [D("1000"), D("800")])

    assert surcharge == D("0.037")


def test_surcharge_is_zero_without_dryer_or_without_printer() -> None:
    assert devices.drying_surcharge([], [D("1000")]) == D("0")
    assert devices.drying_surcharge([D("26.60")], []) == D("0")


# --- Residual value (cases 9-10) ---------------------------------------------------------------


def test_residual_value_decreases_by_the_day() -> None:
    today = date(2026, 11, 16)

    value = devices.residual_value(D("749"), D("5"), today - timedelta(days=372), today)

    assert value.quantize(D("0.01")) == D("596.33")


def test_residual_value_is_lower_a_day_later() -> None:
    bought = date(2026, 1, 1)

    first = devices.residual_value(D("749"), D("5"), bought, date(2026, 6, 1))
    later = devices.residual_value(D("749"), D("5"), bought, date(2026, 6, 2))

    assert later < first


def test_residual_value_is_zero_after_the_useful_life() -> None:
    bought = date(2020, 1, 1)

    assert devices.residual_value(D("749"), D("5"), bought, date(2026, 11, 16)) == D("0")


def test_residual_value_is_the_full_price_for_a_future_purchase() -> None:
    today = date(2026, 11, 16)

    value = devices.residual_value(D("749"), D("5"), today + timedelta(days=10), today)

    assert value == D("749")


# --- Form check (cases 11-13) ------------------------------------------------------------------


def valid(**overrides: str) -> dict[str, str]:
    raw = {
        "name": "Drucker A",
        "bought_on": "2025-11-09",
        "price": "749,00",
        "years": "5",
        "hours_per_year": "1000",
        "maintenance_pct": "35",
    }
    raw.update(overrides)
    return raw


def test_valid_printer_form_gives_exact_decimals() -> None:
    values, errors = device_form.validate("printer", valid())

    assert errors == {}
    assert values["price"] == D("749.00")
    assert values["hours_per_year"] == D("1000")
    assert values["bought_on"] == date(2025, 11, 9)
    assert values["maintenance_pct"] == D("35")


def test_dryer_form_needs_no_hours() -> None:
    raw = valid()
    del raw["hours_per_year"]

    values, errors = device_form.validate("dryer", raw)

    assert errors == {}
    assert values["hours_per_year"] is None


@pytest.mark.parametrize("field", ["price", "years"])
@pytest.mark.parametrize("bad", ["0", "0,00", "-1", ""])
def test_price_and_years_must_be_above_zero(field: str, bad: str) -> None:
    _, errors = device_form.validate("printer", valid(**{field: bad}))

    assert field in errors


@pytest.mark.parametrize("bad", ["0", "-5", ""])
def test_a_printer_needs_print_hours_above_zero(bad: str) -> None:
    _, errors = device_form.validate("printer", valid(hours_per_year=bad))

    assert "hours_per_year" in errors


def test_maintenance_may_be_zero_but_not_negative() -> None:
    assert device_form.validate("printer", valid(maintenance_pct="0"))[1] == {}
    assert "maintenance_pct" in device_form.validate("printer", valid(maintenance_pct="-1"))[1]


@pytest.mark.parametrize("bad", ["abc", "1e3", "1,2,3", "5 Jahre"])
def test_numbers_must_be_plain_numbers(bad: str) -> None:
    assert "price" in device_form.validate("printer", valid(price=bad))[1]


def test_name_is_required_and_limited() -> None:
    assert "name" in device_form.validate("printer", valid(name="  "))[1]
    assert "name" in device_form.validate("printer", valid(name="x" * 101))[1]


@pytest.mark.parametrize("bad", ["", "09.11.2025", "2025-13-40", "morgen"])
def test_purchase_date_must_be_a_real_date(bad: str) -> None:
    assert "bought_on" in device_form.validate("printer", valid(bought_on=bad))[1]


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        device_form.validate("scanner", valid())


# --- Migration (case 18) -----------------------------------------------------------------------


def test_migration_creates_the_devices_table_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "devices.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0019")

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO devices (kind, name, bought_on, price, years, hours_per_year,"
            " maintenance_pct, active, created_at)"
            " VALUES ('printer', 'A', '2025-11-09', '749', '5', '1000', '35', 1,"
            " '2026-11-16 10:00:00')"
        )
        assert connection.execute("SELECT name, price FROM devices").fetchall() == [("A", "749")]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO devices (kind, name, bought_on, price, years, maintenance_pct,"
                " active, created_at) VALUES ('scanner', 'B', '2025-11-09', '1', '1', '0', 1,"
                " '2026-11-16 10:00:00')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO devices (kind, name, bought_on, price, years, maintenance_pct,"
                " active, created_at) VALUES ('printer', 'C', '2025-11-09', '1', '1', '0', 1,"
                " '2026-11-16 10:00:00')"
            )
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0019")
    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'devices'"
        ).fetchall()
        assert tables == []
    run_migrations(engine)
