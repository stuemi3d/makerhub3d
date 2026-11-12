from collections.abc import Iterator
from pathlib import Path

import pytest
from argon2 import PasswordHasher

from app import auth
from app.main import create_app
from tests.auth import AppClient, signed_in_client


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Every test gets its own empty data directory, so no test writes into the repository.
    path = tmp_path / "data"
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
