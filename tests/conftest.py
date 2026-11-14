import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from argon2 import PasswordHasher

from app import auth
from app.config import DATABASE_FILE
from app.db import make_engine, run_migrations
from app.main import create_app
from tests.auth import AppClient, signed_in_client


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One empty database, migrated once per test run. Tests only ever get copies of it."""
    path = tmp_path_factory.mktemp("database-template") / DATABASE_FILE
    engine = make_engine(path)
    run_migrations(engine)
    engine.dispose()
    return path


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, migrated_template: Path) -> Path:
    # Every test gets its own data directory, so no test writes into the repository. It holds
    # a copy of the migrated empty database: the app then finds it up to date at its start
    # instead of running every migration again for each of the many tests.
    path = tmp_path / "data"
    path.mkdir()
    shutil.copyfile(migrated_template, path / DATABASE_FILE)
    monkeypatch.setenv("MAKERHUB_DATA_DIR", str(path))
    return path


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Still Argon2id, but with minimal effort so that the many logins in tests stay fast.
    monkeypatch.setattr(
        auth, "PASSWORD_HASHER", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )


@pytest.fixture
def client() -> Iterator[AppClient]:
    """Logged in as the first admin. The app's startup (migrations) runs like a real start."""
    with signed_in_client() as test_client:
        yield test_client


@pytest.fixture
def anon_client() -> Iterator[AppClient]:
    """Not logged in; no user exists yet."""
    with AppClient(create_app()) as test_client:
        yield test_client
