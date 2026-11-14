"""Every test starts from a copy of one migrated, empty database (see tests/conftest.py).

The tests in this file depend on running in file order (a writer comes before its checker).
"""

import sqlite3
from pathlib import Path

from sqlalchemy.orm import Session

from app import auth
from app.config import DATABASE_FILE, Settings
from app.db import head_revision, make_engine, run_migrations
from app.main import create_app
from tests.auth import AppClient, add_user


def _schema(path: Path) -> dict[str, tuple]:
    """Tables with their columns, plus index names. (The CREATE text itself is not stable.)"""
    with sqlite3.connect(path) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        schema = {
            name: tuple(connection.execute(f'PRAGMA table_info("{name}")').fetchall())
            for name in names
        }
        schema["#indexes"] = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name"
            )
        )
    return schema


def _version(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def _users_exist() -> bool:
    engine = make_engine(Settings.from_env().database_path)
    try:
        with Session(engine) as db:
            return auth.has_users(db)
    finally:
        engine.dispose()


def test_first_test_writes_a_user() -> None:
    add_user("schreiber", "admin")
    assert _users_exist()


def test_next_test_does_not_see_that_user() -> None:
    # Case 1: tests never see the data of the test before.
    assert not _users_exist()


def test_template_stays_empty_after_a_test_wrote_data(migrated_template: Path) -> None:
    # Case 2: the shared template is never written to.
    with sqlite3.connect(migrated_template) as connection:
        count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert count == 0


def test_copy_equals_a_freshly_migrated_database(
    data_dir: Path, tmp_path_factory, migrated_template: Path
) -> None:
    # Case 3: same tables and same migration version as a real fresh migration.
    fresh = tmp_path_factory.mktemp("fresh") / DATABASE_FILE
    engine = make_engine(fresh)
    run_migrations(engine)
    engine.dispose()
    copy = data_dir / DATABASE_FILE
    assert copy.exists()
    assert _schema(copy) == _schema(fresh)
    assert _version(copy) == head_revision()


def test_real_start_without_a_database_still_migrates(data_dir: Path) -> None:
    # Case 4: the normal start (empty data folder, no database file) runs the migrations.
    (data_dir / DATABASE_FILE).unlink()
    with AppClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
    assert _version(data_dir / DATABASE_FILE) == head_revision()


def test_a_test_running_alone_gets_a_clean_database() -> None:
    # Case 5: no leftovers, whatever ran before.
    assert not _users_exist()
