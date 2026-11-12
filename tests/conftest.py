from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Every test gets its own empty data directory, so no test writes into the repository.
    path = tmp_path / "data"
    monkeypatch.setenv("MAKERHUB_DATA_DIR", str(path))
    return path


@pytest.fixture
def client() -> Iterator[TestClient]:
    # The context manager runs the app's startup (database migrations) like a real start.
    with TestClient(create_app()) as test_client:
        yield test_client
