"""Bambuddy connection in the settings (issue #117, test cases 11-15)."""

import json
import sqlite3
from pathlib import Path

import httpx2
import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import alembic_config, make_engine
from app.main import create_app
from app.models import BambuddyConnection, ChangeLogEntry
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as, signed_in_client

TEST_SECRET_KEY = "x" * 40
KEY = "test-key-nur-fuer-tests"
URL = "http://bambuddy.example.com:8000"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bambuddy"
ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def two_factor_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Bambuddy key is stored encrypted with the app's key.
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


def stored() -> BambuddyConnection:
    with Session(make_engine(Settings.from_env().database_path)) as session:
        return session.get(BambuddyConnection, 1)


def log_entries() -> list[ChangeLogEntry]:
    with Session(make_engine(Settings.from_env().database_path)) as session:
        return list(session.scalars(select(ChangeLogEntry)))


def answer_with(client: AppClient, handler) -> None:
    client.app.state.bambuddy_transport = httpx2.MockTransport(handler)


def good_handler(request: httpx2.Request) -> httpx2.Response:
    routes = {
        "/api/v1/archives/": "archives.json",
        "/api/v1/inventory/spools": "spools.json",
        "/api/v1/printers/": "printers.json",
        "/api/v1/settings/": "settings.json",
    }
    body = json.loads((FIXTURES / routes[request.url.path]).read_text(encoding="utf-8"))
    return httpx2.Response(200, json=body)


def save(client: AppClient, **form: str):
    # The form always sends the start date (issue #119)
    data = {"url": URL, "api_key": KEY, "prints_from": "2026-01-01", **form}
    return client.post("/settings/bambuddy", data=data, follow_redirects=False)


# --- Test case 11: only admins -------------------------------------------------------------


def test_admin_sees_the_page_and_the_tab(client: AppClient) -> None:
    page = client.get("/settings/bambuddy")
    assert page.status_code == 200
    assert DE["settings.tab.bambuddy"] in page.text
    assert 'href="/settings/bambuddy"' in client.get("/settings").text


@pytest.mark.parametrize("name", [EMPLOYEE_NAME, READER_NAME])
def test_others_get_no_permission_page(client: AppClient, name: str) -> None:
    add_user(name, "employee" if name == EMPLOYEE_NAME else "read_only")
    with client_as(name) as other:
        assert other.get("/settings/bambuddy").status_code == 403
        assert save(other).status_code == 403
        assert other.post("/settings/bambuddy/test", data={}).status_code == 403
    assert stored().url == ""


# --- Saving, key never shown (test case 12) --------------------------------------------------


def test_saving_stores_url_and_an_encrypted_key(client: AppClient) -> None:
    assert save(client).status_code == 303
    row = stored()
    assert row.url == URL
    assert row.api_key_encrypted and KEY not in row.api_key_encrypted


def test_the_key_is_never_shown_again(client: AppClient) -> None:
    save(client)
    page = client.get("/settings/bambuddy").text
    assert KEY not in page
    assert DE["bambuddy.key.set"] in page
    assert URL in page


def test_an_error_page_does_not_send_the_key_back(client: AppClient) -> None:
    response = save(client, url="ftp://nope")
    assert response.status_code == 422
    assert KEY not in response.text


def test_empty_key_field_keeps_the_stored_key(client: AppClient) -> None:
    save(client)
    before = stored().api_key_encrypted
    assert save(client, api_key="").status_code == 303
    assert stored().api_key_encrypted == before


def test_a_changed_address_without_a_new_key_drops_the_old_key(client: AppClient) -> None:
    save(client)
    assert save(client, url="http://127.0.0.1:8000", api_key="").status_code == 303
    assert stored().url == "http://127.0.0.1:8000"
    assert stored().api_key_encrypted is None


def test_url_is_stored_without_trailing_slash(client: AppClient) -> None:
    save(client, url=URL + "/")
    assert stored().url == URL


# --- Test case 14: invalid addresses ----------------------------------------------------------


@pytest.mark.parametrize("url", ["ftp://x.example.com", "bambuddy.example.com", "http://", ""])
def test_invalid_url_is_refused_and_nothing_is_stored(client: AppClient, url: str) -> None:
    response = save(client, url=url)
    assert response.status_code == 422
    assert stored().url == ""


def test_without_the_app_key_the_bambuddy_key_cannot_be_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAKERHUB_SECRET_KEY")
    with signed_in_client(create_app()) as client:
        response = save(client)
        assert response.status_code == 422
        assert DE["bambuddy.error.no_cipher"] in response.text
        assert stored().api_key_encrypted is None


# --- Test case 15: change log ----------------------------------------------------------------


def test_saving_is_logged_without_the_key(client: AppClient) -> None:
    save(client)
    entries = [e for e in log_entries() if e.action == "bambuddy.changed"]
    assert len(entries) == 1
    details = entries[0].details
    assert URL in details and "api_key" in details
    assert KEY not in details and KEY not in entries[0].subject


def test_saving_without_change_leaves_no_log_entry(client: AppClient) -> None:
    save(client)
    save(client, api_key="")
    assert len([e for e in log_entries() if e.action == "bambuddy.changed"]) == 1


# --- Test cases 8, 9, 13: the connection test ----------------------------------------------------


def test_connection_test_says_ok(client: AppClient) -> None:
    save(client)
    answer_with(client, good_handler)
    page = client.post("/settings/bambuddy/test", data={})
    assert page.status_code == 200
    assert DE["bambuddy.test.ok"] in page.text


def test_connection_test_only_calls_the_saved_address(client: AppClient) -> None:
    save(client)
    hosts: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        hosts.append(request.url.host)
        return good_handler(request)

    answer_with(client, handler)
    client.post("/settings/bambuddy/test", data={"url": "http://evil.example.org"})
    assert set(hosts) == {"bambuddy.example.com"}


def test_connection_test_reports_a_rejected_key(client: AppClient) -> None:
    save(client)
    answer_with(client, lambda request: httpx2.Response(401, json={"detail": "no"}))
    page = client.post("/settings/bambuddy/test", data={})
    assert DE["bambuddy.error.rejected"] in page.text
    assert KEY not in page.text


def test_connection_test_names_the_missing_right(client: AppClient) -> None:
    save(client)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/v1/inventory/spools":
            return httpx2.Response(403, json={"detail": "no"})
        return good_handler(request)

    answer_with(client, handler)
    page = client.post("/settings/bambuddy/test", data={})
    assert DE["bambuddy.error.forbidden"] in page.text
    assert "/api/v1/inventory/spools" in page.text


def test_connection_test_hides_internal_details(client: AppClient) -> None:
    save(client)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"refused {URL} {KEY}")

    answer_with(client, handler)
    page = client.post("/settings/bambuddy/test", data={})
    assert DE["bambuddy.error.unreachable"] in page.text
    assert KEY not in page.text and "refused" not in page.text


def test_connection_test_without_saved_settings(client: AppClient) -> None:
    page = client.post("/settings/bambuddy/test", data={})
    assert page.status_code == 200
    assert DE["bambuddy.error.not_configured"] in page.text


# --- Migration -------------------------------------------------------------------------------


def test_migration_adds_an_empty_row_and_can_be_reverted(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "migration.db")
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0011")
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0012")
    with sqlite3.connect(tmp_path / "migration.db") as raw:
        assert raw.execute(
            "SELECT id, url, api_key_encrypted FROM bambuddy_connection"
        ).fetchall() == [(1, "", None)]
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0011")
    with sqlite3.connect(tmp_path / "migration.db") as raw:
        names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "bambuddy_connection" not in names


def test_connection_test_reports_an_unreadable_key(
    client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(client)
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", "y" * 40)
    with signed_in_client(create_app()) as other_start:
        page = other_start.post("/settings/bambuddy/test", data={})
    assert DE["bambuddy.error.key_unreadable"] in page.text
