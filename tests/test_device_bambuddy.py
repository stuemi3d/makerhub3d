"""Devices: taking a printer over from Bambuddy and deleting a device (issue #163).

All data is invented; Bambuddy answers come from a mock (the recorded printer list has one
printer "Beispiel-Drucker" with the id 1). The numbers refer to the comments on the issue.
"""

import sqlite3
from pathlib import Path

import httpx2
import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import device_usage
from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.models import ChangeLogEntry
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_settings import (
    TEST_SECRET_KEY,
    answer_with,
    good_handler,
    save,
)
from tests.test_device_pages import add, dryer_form, printer_form, stored_devices

NAME = "Beispiel-Drucker"
NEW = "/settings/devices/new?kind=printer"
PICK = NEW + "&bambuddy_id=1"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def linked(client: AppClient) -> AppClient:
    """Admin client with a working Bambuddy connection."""
    answer_with(client, good_handler)
    save(client)
    return client


def take_over(client: AppClient, **overrides: str):
    form = printer_form(bambuddy_printer_id="1", **overrides)
    return client.post("/settings/devices/new", data=form)


def log_actions() -> list[str]:
    with Session(make_engine(Settings.from_env().database_path)) as db:
        actions = db.scalars(select(ChangeLogEntry.action).order_by(ChangeLogEntry.id))
        return [action for action in actions if action.startswith("device.")]


# --- Taking a printer over (cases 1-3) ---------------------------------------------------------


def test_new_printer_offers_the_printers_of_bambuddy(linked: AppClient) -> None:
    page = linked.get(NEW).text

    assert NAME in page
    assert "bambuddy_id=1" in page
    assert "manual=1" in page


def test_chosen_printer_has_its_name_filled_in_and_locked(linked: AppClient) -> None:
    page = linked.get(PICK).text

    assert f'value="{NAME}"' in page
    assert "readonly" in page
    assert 'name="bambuddy_printer_id" value="1"' in page


def test_taken_over_printer_is_saved_with_the_name_of_bambuddy(linked: AppClient) -> None:
    response = take_over(linked, name="Anderer Name")

    assert response.status_code in (200, 204, 303)
    device = stored_devices()[0]
    assert (device.name, device.bambuddy_printer_id) == (NAME, 1)
    page = linked.get("/settings/devices").text
    assert NAME in page
    assert "0,2022" in page


def test_a_taken_over_printer_is_not_offered_again(linked: AppClient) -> None:
    take_over(linked)

    page = linked.get(NEW).text

    assert "bambuddy_id=1" not in page
    assert 'id="name"' in page


def test_the_same_bambuddy_printer_cannot_be_taken_twice(linked: AppClient) -> None:
    take_over(linked)

    response = take_over(linked)

    assert response.status_code == 422
    assert len(stored_devices()) == 1


# --- The name is locked (case 4) ---------------------------------------------------------------


def test_the_name_of_a_taken_over_printer_cannot_be_changed(linked: AppClient) -> None:
    take_over(linked)
    device = stored_devices()[0]
    edit = f"/settings/devices/{device.id}/edit"

    assert "readonly" in linked.get(edit).text
    linked.post(edit, data=printer_form(name="Umbenannt", years="4"))

    changed = stored_devices()[0]
    assert (changed.name, changed.years) == (NAME, 4)


# --- Without Bambuddy (cases 5, 6) -------------------------------------------------------------


def test_without_a_connection_the_printer_is_created_by_hand_with_a_hint(
    client: AppClient,
) -> None:
    page = client.get(NEW).text

    assert 'id="name"' in page
    assert "device.import" not in page
    assert "Bambuddy" in page
    add(client, printer_form())
    assert stored_devices()[0].bambuddy_printer_id is None


def test_unreachable_bambuddy_gives_a_hint_and_by_hand_still_works(linked: AppClient) -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("boom")

    answer_with(linked, down)

    page = linked.get(NEW).text

    assert "nicht erreichbar" in page
    assert 'id="name"' in page
    add(linked, printer_form())
    assert [d.name for d in stored_devices()] == ["Drucker A"]


def test_manual_choice_shows_the_empty_form(linked: AppClient) -> None:
    page = linked.get(NEW + "&manual=1").text

    assert 'id="name"' in page
    assert "readonly" not in page


def test_dryers_stay_manual(linked: AppClient) -> None:
    page = linked.get("/settings/devices/new?kind=dryer").text
    assert "bambuddy_id" not in page

    # A dryer is not a Bambuddy printer: the id is refused, nothing is saved.
    response = linked.post("/settings/devices/new", data=dryer_form(bambuddy_printer_id="1"))

    assert response.status_code == 422
    assert stored_devices() == []


# --- Check on the server (case 10) -------------------------------------------------------------


def test_an_id_bambuddy_does_not_deliver_is_rejected(linked: AppClient) -> None:
    response = linked.post("/settings/devices/new", data=printer_form(bambuddy_printer_id="99"))

    assert response.status_code == 422
    assert stored_devices() == []


def test_a_wrong_id_is_rejected_not_taken_as_by_hand(linked: AppClient) -> None:
    response = linked.post("/settings/devices/new", data=printer_form(bambuddy_printer_id="abc"))

    assert response.status_code == 422
    assert stored_devices() == []


@pytest.mark.parametrize("name", [EMPLOYEE_NAME, READER_NAME])
def test_others_cannot_see_or_use_the_choice(linked: AppClient, name: str) -> None:
    add_user(name, "employee" if name == EMPLOYEE_NAME else "read_only")
    with client_as(name) as other:
        other.app.state.bambuddy_transport = linked.app.state.bambuddy_transport
        assert other.get(PICK).status_code == 403
        assert take_over(other).status_code == 403
    assert stored_devices() == []


# --- Read only (case 7) and log (case 9) -------------------------------------------------------


def test_bambuddy_is_only_read(client: AppClient) -> None:
    methods: list[str] = []

    def spy(request: httpx2.Request) -> httpx2.Response:
        methods.append(request.method)
        return good_handler(request)

    answer_with(client, spy)
    save(client)
    client.get(NEW)
    take_over(client)

    assert methods and set(methods) == {"GET"}


def test_taking_over_and_deleting_are_in_the_change_log(linked: AppClient) -> None:
    take_over(linked)
    device = stored_devices()[0]
    linked.post(f"/settings/devices/{device.id}/delete", data={})

    assert log_actions() == ["device.created", "device.deleted"]


# --- Delete (cases 13-17) ----------------------------------------------------------------------


def test_a_device_without_productions_can_be_deleted(client: AppClient) -> None:
    add(client, printer_form())
    add(client, dryer_form())
    printer = stored_devices()[0]
    page = client.get("/settings/devices").text
    assert f"/settings/devices/{printer.id}/delete" in page

    response = client.post(f"/settings/devices/{printer.id}/delete", data={})

    assert response.status_code in (200, 303)
    assert [d.name for d in stored_devices()] == ["Trockner A"]
    assert f"/settings/devices/{printer.id}/delete" not in client.get("/settings/devices").text


def test_a_deleted_printer_no_longer_counts_for_the_surcharge(client: AppClient) -> None:
    add(client, printer_form(maintenance_pct="0", price="1000"))
    add(client, dryer_form())
    assert "0,0266" in client.get("/settings/devices").text

    client.post(f"/settings/devices/{stored_devices()[0].id}/delete", data={})

    assert "0,0266" not in client.get("/settings/devices").text


def test_a_device_with_productions_cannot_be_deleted(
    client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(client, printer_form())
    printer = stored_devices()[0]
    monkeypatch.setattr(device_usage, "is_in_use", lambda db, device_id: True)

    page = client.get("/settings/devices").text
    response = client.post(f"/settings/devices/{printer.id}/delete", data={})

    assert f"/settings/devices/{printer.id}/delete" not in page
    assert response.status_code == 409
    assert len(stored_devices()) == 1


def test_today_no_production_hangs_on_a_device() -> None:
    # The connection comes with issue #162; until then nothing is in use.
    engine = make_engine(Settings.from_env().database_path)
    run_migrations(engine)
    with Session(engine) as db:
        assert device_usage.is_in_use(db, 1) is False


def test_a_deleted_bambuddy_printer_is_offered_again(linked: AppClient) -> None:
    take_over(linked)
    linked.post(f"/settings/devices/{stored_devices()[0].id}/delete", data={})

    assert "bambuddy_id=1" in linked.get(NEW).text


def test_deleting_an_unknown_device_is_not_found(client: AppClient) -> None:
    assert client.post("/settings/devices/999/delete", data={}).status_code == 404


@pytest.mark.parametrize("name", [EMPLOYEE_NAME, READER_NAME])
def test_others_cannot_delete(client: AppClient, name: str) -> None:
    add(client, printer_form())
    add_user(name, "employee" if name == EMPLOYEE_NAME else "read_only")
    with client_as(name) as other:
        assert (
            other.post(f"/settings/devices/{stored_devices()[0].id}/delete", data={}).status_code
            == 403
        )
    assert len(stored_devices()) == 1


def test_deleting_needs_the_form_token(client: AppClient) -> None:
    add(client, printer_form())
    client.csrf_token = None

    response = client.post(f"/settings/devices/{stored_devices()[0].id}/delete", data={})

    assert response.status_code == 403
    assert len(stored_devices()) == 1


def test_the_delete_button_asks_first(client: AppClient) -> None:
    add(client, printer_form())

    assert "data-confirm=" in client.get("/settings/devices").text


# --- Migration (case 12) -----------------------------------------------------------------------


def test_migration_adds_the_bambuddy_id_once_per_printer_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "devices.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0020")

    run_migrations(engine)

    insert = (
        "INSERT INTO devices (kind, name, bought_on, price, years, hours_per_year,"
        " maintenance_pct, active, created_at, bambuddy_printer_id)"
        " VALUES ('printer', ?, '2025-11-09', '749', '5', '1000', '35', 1,"
        " '2026-11-16 10:00:00', ?)"
    )
    with sqlite3.connect(database) as connection:
        connection.execute(insert, ("A", 1))
        connection.execute(insert, ("B", None))
        connection.execute(insert, ("C", None))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("D", 1))
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0020")
    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(devices)")]
        assert "bambuddy_printer_id" not in columns
        assert connection.execute("SELECT COUNT(*) FROM devices").fetchone() == (3,)
    run_migrations(engine)


def test_a_race_for_the_same_printer_gives_the_error_not_a_crash(
    linked: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bambuddy still offers the printer (the other admin saved a moment ago): the unique
    # index of the database catches the second one.
    take_over(linked)
    from app import device_import

    monkeypatch.setattr(
        device_import.Choice, "find", lambda self, printer_id: device_import.Printer(1, NAME, None)
    )

    response = take_over(linked)

    assert response.status_code == 422
    assert len(stored_devices()) == 1
