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
from app.db import (
    DATABASE_FILE,
    Base,
    MigrationError,
    alembic_config,
    make_engine,
    run_migrations,
)
from app.main import create_app
from app.models import AppSettings, Rounding
from tests.auth import signed_in_client
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
    with signed_in_client() as client:
        client.post("/settings", data=valid_form(electricity_price="0,3215"))

    with signed_in_client() as client:
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


# --- Issue #8: user and session tables ------------------------------------------------


def test_login_tables_are_created(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "login.db")
    run_migrations(engine)

    tables = inspect(engine).get_table_names()
    assert "users" in tables
    assert "login_sessions" in tables


def test_login_migration_keeps_existing_settings(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "upgrade.db")
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0001")
    with Session(engine) as session, session.begin():
        session.get(AppSettings, 1).markup = Decimal("42")

    run_migrations(engine)

    with Session(engine) as session:
        assert session.get(AppSettings, 1).markup == Decimal("42")
    assert "users" in inspect(engine).get_table_names()


def test_deleting_a_user_also_deletes_their_sessions(tmp_path: Path) -> None:
    database = tmp_path / "cascade.db"
    run_migrations(make_engine(database))
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO users"
            " (id, username, username_key, password_hash, role, failed_logins, created_at)"
            " VALUES (1, 'erfunden', 'erfunden', 'x', 'admin', 0, '2026-01-01 00:00:00')"
        )
        connection.execute(
            "INSERT INTO login_sessions (id, user_id, csrf_token, created_at, last_seen_at)"
            " VALUES ('abc', 1, 't', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        connection.execute("DELETE FROM users")
        assert connection.execute("SELECT count(*) FROM login_sessions").fetchone() == (0,)


def test_unknown_role_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "role.db"
    run_migrations(make_engine(database))
    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO users"
            " (username, username_key, password_hash, role, failed_logins, created_at)"
            " VALUES ('erfunden', 'erfunden', 'x', 'chef', 0, '2026-01-01 00:00:00')"
        )


# --- Issue #28: active flag and case-insensitive names --------------------------------


def test_existing_users_stay_active_and_get_a_name_key(tmp_path: Path) -> None:
    database = tmp_path / "users.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0002")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (username, password_hash, role, failed_logins, created_at)"
            " VALUES ('Jörg-Erfunden', 'x', 'admin', 0, '2026-01-01 00:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT username, username_key, active FROM users").fetchone()
    assert row == ("Jörg-Erfunden", "jörg-erfunden", 1)


def test_names_differing_only_in_case_are_refused(tmp_path: Path) -> None:
    database = tmp_path / "unique.db"
    run_migrations(make_engine(database))
    insert = (
        "INSERT INTO users"
        " (username, username_key, password_hash, role, failed_logins, created_at)"
        " VALUES (?, ?, 'x', 'admin', 0, '2026-01-01 00:00:00')"
    )
    with sqlite3.connect(database) as connection:
        connection.execute(insert, ("Anna", "anna"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("ANNA", "anna"))


def test_user_migration_can_be_undone(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "undo.db")
    run_migrations(engine)

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0002")
        columns = {column["name"] for column in inspect(connection).get_columns("users")}

    assert "active" not in columns
    assert "username_key" not in columns


def test_migrations_keep_rows_of_dependent_tables(tmp_path: Path) -> None:
    # SQLite rebuilds a changed table; that must not delete rows that point to it.
    database = tmp_path / "children.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0002")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, role, failed_logins, created_at)"
            " VALUES (1, 'erfunden', 'x', 'admin', 0, '2026-01-01 00:00:00')"
        )
        connection.execute(
            "INSERT INTO login_sessions (id, user_id, csrf_token, created_at, last_seen_at)"
            " VALUES ('abc', 1, 't', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM login_sessions").fetchone() == (1,)


def test_foreign_keys_are_on_again_after_migrating(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "pragma.db")
    run_migrations(engine)

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_broken_references_stop_the_migration(tmp_path: Path) -> None:
    database = tmp_path / "broken.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0002")
    with sqlite3.connect(database) as connection:
        # Foreign keys are off in a plain sqlite3 connection, so this orphan gets in.
        connection.execute(
            "INSERT INTO login_sessions (id, user_id, csrf_token, created_at, last_seen_at)"
            " VALUES ('abc', 99, 't', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )

    with pytest.raises(MigrationError):
        run_migrations(engine)


# --- Issue #32: duty to change an initial password ------------------------------------


def test_existing_users_get_no_duty_to_change(tmp_path: Path) -> None:
    database = tmp_path / "duty.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0003")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users"
            " (username, username_key, password_hash, role, failed_logins, created_at)"
            " VALUES ('erfunden', 'erfunden', 'x', 'admin', 0, '2026-01-01 00:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT must_change_password FROM users").fetchone() == (0,)


# --- Issue #29: change log and login log ----------------------------------------------


def test_log_tables_are_created_and_keep_users(tmp_path: Path) -> None:
    database = tmp_path / "log.db"
    engine = make_engine(database)
    run_migrations(engine)

    tables = inspect(engine).get_table_names()
    assert "change_log" in tables
    assert "login_log" in tables
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO users"
            " (id, username, username_key, password_hash, role, failed_logins, created_at)"
            " VALUES (1, 'erfunden', 'erfunden', 'x', 'admin', 0, '2026-01-01 00:00:00')"
        )
        connection.execute(
            "INSERT INTO change_log (created_at, user_id, action, subject, details)"
            " VALUES ('2026-01-01 00:00:00', 1, 'settings.changed', '', '[]')"
        )
        # Users with log entries cannot be deleted by accident.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM users")


# --- Issue #30: two-factor login --------------------------------------------------------


def test_two_factor_migration_keeps_users_and_logs(tmp_path: Path) -> None:
    database = tmp_path / "two_factor.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0005")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users"
            " (id, username, username_key, password_hash, role, failed_logins, created_at)"
            " VALUES (1, 'erfunden', 'erfunden', 'x', 'admin', 0, '2026-01-01 00:00:00')"
        )
        connection.execute(
            "INSERT INTO login_log (created_at, user_id, event)"
            " VALUES ('2026-01-01 00:00:00', 1, 'success')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        # Existing users start without two-factor login; log entries stay.
        assert connection.execute(
            "SELECT username, totp_secret, totp_pending_secret, totp_last_step FROM users"
        ).fetchall() == [("erfunden", None, None, None)]
        assert connection.execute("SELECT event FROM login_log").fetchall() == [("success",)]
        for event in ("wrong_code", "recovery_code_used"):
            connection.execute(
                "INSERT INTO login_log (created_at, user_id, event)"
                " VALUES ('2026-01-01 00:00:00', 1, ?)",
                (event,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO login_log (created_at, user_id, event)"
                " VALUES ('2026-01-01 00:00:00', 1, 'erfunden')"
            )
        # Commands on the server write change log entries without a user.
        connection.execute(
            "INSERT INTO change_log (created_at, user_id, action, subject, details)"
            " VALUES ('2026-01-01 00:00:00', NULL, 'system.2fa_reset_all', '', '[]')"
        )


def test_two_factor_migration_can_be_undone(tmp_path: Path) -> None:
    database = tmp_path / "two_factor_down.db"
    engine = make_engine(database)
    run_migrations(engine)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users"
            " (id, username, username_key, password_hash, role, failed_logins, created_at)"
            " VALUES (1, 'erfunden', 'erfunden', 'x', 'admin', 0, '2026-01-01 00:00:00')"
        )
        connection.execute(
            "INSERT INTO login_log (created_at, user_id, event)"
            " VALUES ('2026-01-01 00:00:00', 1, 'wrong_code')"
        )
        connection.execute(
            "INSERT INTO change_log (created_at, user_id, action, subject, details)"
            " VALUES ('2026-01-01 00:00:00', NULL, 'system.2fa_reset_all', '', '[]')"
        )

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0005")

    tables = inspect(engine).get_table_names()
    assert "recovery_codes" not in tables
    assert "pending_logins" not in tables
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT username FROM users").fetchall() == [("erfunden",)]
        assert connection.execute("SELECT count(*) FROM login_log").fetchone() == (0,)
