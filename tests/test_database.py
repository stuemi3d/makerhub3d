"""Database file, migrations and the match between models and migrations."""

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import DATABASE_FILE, Base, alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import AppSettings, Rounding
from tests.forms import valid_form


def start_app() -> TestClient:
    return TestClient(create_app())


# --- Test case 1: the start creates and migrates the database -------------------------


def test_start_creates_database_file_and_tables(data_dir: Path) -> None:
    assert not data_dir.exists()

    with start_app():
        pass

    database = data_dir / DATABASE_FILE
    assert database.is_file()
    tables = inspect(make_engine(database)).get_table_names()
    assert "app_settings" in tables
    assert "alembic_version" in tables


def test_creating_the_app_alone_does_not_touch_the_disk(data_dir: Path) -> None:
    create_app()

    assert not data_dir.exists()


# --- Test case 2: a second start changes nothing --------------------------------------


def test_second_start_keeps_saved_values(data_dir: Path) -> None:
    with start_app() as client:
        client.post("/settings", data=valid_form(electricity_price="0,3215"))

    with start_app() as client:
        html = client.get("/settings").text

    assert 'value="0,3215"' in html
    with sqlite3.connect(data_dir / DATABASE_FILE) as connection:
        assert connection.execute("SELECT count(*) FROM app_settings").fetchone() == (1,)


# --- Test case 3: location from MAKERHUB_DATA_DIR -------------------------------------


def test_data_dir_comes_from_environment(data_dir: Path) -> None:
    assert Settings.from_env().data_dir == data_dir
    assert Settings.from_env().database_path == data_dir / "makerhub3d.db"


def test_data_dir_defaults_to_local_data_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAKERHUB_DATA_DIR")

    assert Settings.from_env().data_dir == Path("data")


def test_container_uses_data_volume() -> None:
    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text("utf-8")

    assert "MAKERHUB_DATA_DIR=/data" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile


# --- Test case 4: migrations on an empty database -------------------------------------


def test_migrations_run_on_empty_database(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "empty.db")

    run_migrations(engine)

    with Session(engine) as session:
        row = session.get(AppSettings, 1)
    assert row is not None


def test_migrations_can_be_undone_and_redone(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "updown.db")
    run_migrations(engine)

    with engine.begin() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "base")
        assert inspect(connection).get_table_names() == ["alembic_version"]
        command.upgrade(config, "head")
        assert "app_settings" in inspect(connection).get_table_names()


# --- Test case 5: migrations on a filled database -------------------------------------


def test_migrations_keep_existing_values(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "filled.db")
    run_migrations(engine)
    with Session(engine) as session, session.begin():
        row = session.get(AppSettings, 1)
        row.electricity_price = Decimal("0.3215")
        row.spool_category = "Firma"
        row.rounding = Rounding.WHOLE_EURO

    run_migrations(engine)

    with Session(engine) as session:
        row = session.get(AppSettings, 1)
        assert row.electricity_price == Decimal("0.3215")
        assert row.spool_category == "Firma"
        assert row.rounding == Rounding.WHOLE_EURO


# --- Test case 6: models and migrations match -----------------------------------------


def test_models_match_migrations(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "compare.db")
    run_migrations(engine)

    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)

    assert differences == []


# --- Defaults (question 2) and exact storage (test case 9) ----------------------------


def test_first_start_fills_in_the_agreed_defaults(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "defaults.db")
    run_migrations(engine)

    with Session(engine) as session:
        row = session.get(AppSettings, 1)

    assert row.electricity_price == Decimal("0.30")
    assert row.labour_rate == Decimal("15.00")
    assert row.scrap_rate == Decimal("5")
    assert row.markup == Decimal("30")
    assert row.rounding == Rounding.ENDS_90
    assert row.spool_category == "Geschäftlich"
    assert row.deviation_threshold == Decimal("15")


def test_amounts_are_stored_exactly_as_text(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "exact.db")
    run_migrations(engine)
    with Session(engine) as session, session.begin():
        session.get(AppSettings, 1).electricity_price = Decimal("0.1") + Decimal("0.25")

    with sqlite3.connect(tmp_path / "exact.db") as connection:
        stored = connection.execute("SELECT electricity_price FROM app_settings").fetchone()[0]
    assert stored == "0.35"
