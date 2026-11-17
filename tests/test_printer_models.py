"""Printer models with their power table (issue #174): lookup, form check and migration.

All data is invented. The numbers refer to the test cases on the issue.
"""

import sqlite3
from decimal import Decimal
from pathlib import Path

from alembic import command

from app import printer_model_form, printer_models
from app.db import alembic_config, make_engine, run_migrations

D = Decimal
TABLE = {"PLA": D("0.10"), "PETG": D("0.13")}


# --- Lookup for the calculation (cases 17-20) --------------------------------------------------


def test_exact_material_is_found_ignoring_case() -> None:
    assert printer_models.lookup(TABLE, "PLA").kwh_per_hour == D("0.10")
    assert printer_models.lookup(TABLE, " pla ").kwh_per_hour == D("0.10")
    assert printer_models.lookup(TABLE, "PLA").status == "found"


def test_no_exact_match_gives_the_materials_of_the_table_to_choose_from() -> None:
    result = printer_models.lookup(TABLE, "PLA-CF")

    assert result.status == "no_match"
    assert result.kwh_per_hour is None
    assert result.options == ("PETG", "PLA")


def test_no_table_is_reported_for_a_missing_or_empty_table() -> None:
    assert printer_models.lookup(None, "PLA").status == "no_table"
    assert printer_models.lookup({}, "PLA").status == "no_table"
    assert printer_models.lookup({}, "PLA").kwh_per_hour is None


def test_plate_energy_adds_the_materials_by_their_time_share() -> None:
    kwh = printer_models.plate_kwh([(D("2"), D("0.10")), (D("1"), D("0.13"))])

    assert kwh == D("0.33")


# --- Check of the table rows (cases 7, 8) ------------------------------------------------------


def rows(*pairs: tuple[str, str]) -> dict[str, str]:
    raw: dict[str, str] = {}
    for index, (material, value) in enumerate(pairs):
        raw[f"material_{index}"] = material
        raw[f"kwh_{index}"] = value
    return raw


def test_rows_are_read_with_comma_and_point() -> None:
    values, errors = printer_model_form.validate_rows(rows(("PLA", "0,10"), ("PETG", "0.13")))

    assert errors == {}
    assert values == [("PLA", D("0.10")), ("PETG", D("0.13"))]


def test_empty_rows_are_skipped() -> None:
    values, errors = printer_model_form.validate_rows(rows(("PLA", "0,1"), ("", ""), ("  ", "")))

    assert errors == {}
    assert values == [("PLA", D("0.1"))]


def test_value_must_be_a_number_above_zero() -> None:
    for bad in ("0", "-1", "abc", "", "1e3"):
        _, errors = printer_model_form.validate_rows(rows(("PLA", bad)))
        assert "kwh_0" in errors, bad


def test_material_must_not_be_empty_when_a_value_is_given() -> None:
    _, errors = printer_model_form.validate_rows(rows(("", "0,1")))

    assert "material_0" in errors


def test_the_same_material_twice_is_refused_ignoring_case() -> None:
    _, errors = printer_model_form.validate_rows(rows(("PLA", "0,1"), ("pla", "0,2")))

    assert "material_1" in errors


def test_material_name_is_limited() -> None:
    _, errors = printer_model_form.validate_rows(rows(("x" * 51, "0,1")))

    assert "material_0" in errors


# --- Migration (case 21) -----------------------------------------------------------------------


def test_migration_keeps_devices_and_adds_models_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "models.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0021")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO devices (kind, name, bought_on, price, years, hours_per_year,"
            " maintenance_pct, active, created_at)"
            " VALUES ('printer', 'Alt', '2025-11-09', '749', '5', '1000', '35', 1,"
            " '2026-11-16 10:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT name, price, printer_model_id FROM devices").fetchone()
        assert row == ("Alt", "749", None)
        assert connection.execute("SELECT count(*) FROM printer_models").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM printer_model_powers").fetchone() == (0,)
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0021")
    with sqlite3.connect(database) as connection:
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(devices)")}
        assert "printer_models" not in tables
        assert "printer_model_id" not in columns
        assert connection.execute("SELECT name FROM devices").fetchall() == [("Alt",)]
    run_migrations(engine)
