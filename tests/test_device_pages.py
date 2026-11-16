"""The settings tab "Geräte" (issue #157): create, change, switch off, rights and log."""

import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.main import create_app
from app.models import Device
from tests.auth import (
    EMPLOYEE_NAME,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
)


def printer_form(**overrides: str) -> dict[str, str]:
    form = {
        "kind": "printer",
        "name": "Drucker A",
        "bought_on": (date.today() - timedelta(days=372)).isoformat(),
        "price": "749,00",
        "years": "5",
        "hours_per_year": "1000",
        "maintenance_pct": "35",
    }
    form.update(overrides)
    return form


def dryer_form(**overrides: str) -> dict[str, str]:
    form = {
        "kind": "dryer",
        "name": "Trockner A",
        "bought_on": date.today().isoformat(),
        "price": "132,99",
        "years": "5",
        "maintenance_pct": "0",
    }
    form.update(overrides)
    return form


def stored_devices() -> list[Device]:
    engine = make_engine(Settings.from_env().database_path)
    with Session(engine) as db:
        return list(db.scalars(select(Device).order_by(Device.id)))


def add(client: AppClient, form: dict[str, str]) -> None:
    response = client.post("/settings/devices/new", data=form)
    assert response.status_code in (200, 303, 204), response.text


# --- Pages show the numbers (cases 1, 4, 9) ----------------------------------------------------


def test_devices_tab_is_in_the_settings_tab_bar(client: AppClient) -> None:
    page = client.get("/settings/devices").text

    assert 'href="/settings/devices"' in page
    assert 'href="/settings/devices"' in client.get("/settings").text


def test_empty_page_invites_to_create_a_device(client: AppClient) -> None:
    page = client.get("/settings/devices").text

    assert "/settings/devices/new?kind=printer" in page
    assert "/settings/devices/new?kind=dryer" in page


def test_printer_shows_rate_and_residual_value(client: AppClient) -> None:
    add(client, printer_form())

    page = client.get("/settings/devices").text

    assert "Drucker A" in page
    assert "0,2022" in page
    assert "596,33" in page


def test_dryer_shows_yearly_costs_and_the_surcharge_is_shown(client: AppClient) -> None:
    add(client, printer_form(maintenance_pct="0", price="1000"))
    add(client, dryer_form())

    page = client.get("/settings/devices").text

    assert "26,60" in page
    assert "0,0266" in page


# --- Change, switch off and on (case 6) --------------------------------------------------------


def test_changing_a_device_keeps_the_others(client: AppClient) -> None:
    add(client, printer_form())
    add(client, dryer_form())
    printer = stored_devices()[0]

    response = client.post(
        f"/settings/devices/{printer.id}/edit", data=printer_form(name="Drucker B", years="4")
    )

    assert response.status_code in (200, 204, 303)
    devices = stored_devices()
    assert [d.name for d in devices] == ["Drucker B", "Trockner A"]
    assert devices[0].years == 4


def test_a_deactivated_dryer_does_not_count_for_the_surcharge(client: AppClient) -> None:
    add(client, printer_form(maintenance_pct="0", price="1000"))
    add(client, dryer_form())
    dryer = stored_devices()[1]

    client.post(f"/settings/devices/{dryer.id}/active", data={"active": "0"})

    assert stored_devices()[1].active is False
    page = client.get("/settings/devices").text
    assert "0,0266" not in page
    client.post(f"/settings/devices/{dryer.id}/active", data={"active": "1"})
    assert stored_devices()[1].active is True
    assert "0,0266" in client.get("/settings/devices").text


def test_devices_cannot_be_deleted(client: AppClient) -> None:
    add(client, printer_form())
    device = stored_devices()[0]

    assert client.post(f"/settings/devices/{device.id}/delete").status_code in (403, 404, 405)
    assert len(stored_devices()) == 1


# --- Wrong input (cases 11-13) -----------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": "0"},
        {"years": "0"},
        {"hours_per_year": "0"},
        {"maintenance_pct": "-1"},
        {"name": " "},
        {"bought_on": "morgen"},
    ],
)
def test_wrong_input_saves_nothing_and_explains(client: AppClient, overrides: dict) -> None:
    response = client.post("/settings/devices/new", data=printer_form(**overrides))

    assert response.status_code == 422
    assert 'role="alert"' in response.text
    assert stored_devices() == []


def test_wrong_input_in_the_popup_stays_in_the_popup(client: AppClient) -> None:
    response = client.post(
        "/settings/devices/new", data=printer_form(price="0"), headers={"HX-Request": "true"}
    )

    assert response.status_code == 422
    assert "<html" not in response.text


def test_saving_in_the_popup_closes_it(client: AppClient) -> None:
    response = client.post(
        "/settings/devices/new", data=printer_form(), headers={"HX-Request": "true"}
    )

    assert response.status_code == 204
    assert "popup-saved" in response.headers["HX-Trigger"]


def test_unknown_kind_and_unknown_device_are_not_found(client: AppClient) -> None:
    assert client.get("/settings/devices/new?kind=scanner").status_code == 404
    assert (
        client.post("/settings/devices/new", data=printer_form(kind="scanner")).status_code == 404
    )
    assert client.get("/settings/devices/999/edit").status_code == 404


# --- Rights, login and form token (cases 14-15) ------------------------------------------------


@pytest.mark.parametrize("name", [EMPLOYEE_NAME, READER_NAME])
def test_only_the_admin_may_use_the_devices(client: AppClient, name: str) -> None:
    add(client, printer_form())
    device_id = stored_devices()[0].id
    add_user(name, "employee" if name == EMPLOYEE_NAME else "read_only")

    with client_as(name) as other:
        assert other.get("/settings/devices").status_code == 403
        assert other.get("/settings/devices/new?kind=printer").status_code == 403
        assert other.post("/settings/devices/new", data=printer_form()).status_code == 403
        assert (
            other.post(f"/settings/devices/{device_id}/edit", data=printer_form()).status_code
            == 403
        )
        assert (
            other.post(f"/settings/devices/{device_id}/active", data={"active": "0"}).status_code
            == 403
        )
        assert 'href="/settings/devices"' not in other.get("/").text
    assert len(stored_devices()) == 1


def test_devices_need_a_login(anon_client: AppClient) -> None:
    response = anon_client.get("/settings/devices", follow_redirects=False)

    assert response.status_code in (302, 303, 307)
    assert "/settings/devices" not in response.headers["location"].split("next=")[0]


def test_the_form_needs_the_form_token(client: AppClient) -> None:
    form = printer_form()
    client.csrf_token = None

    response = client.post("/settings/devices/new", data=form)

    assert response.status_code == 403
    assert stored_devices() == []


def test_the_form_carries_the_form_token(client: AppClient) -> None:
    page = client.get("/settings/devices/new?kind=printer").text

    assert re.search(r'name="csrf_token" value="[^"]+"', page)


# --- Change log (case 16) ----------------------------------------------------------------------


def test_every_change_is_in_the_change_log(client: AppClient) -> None:
    add(client, printer_form())
    device_id = stored_devices()[0].id
    client.post(f"/settings/devices/{device_id}/edit", data=printer_form(years="4"))
    client.post(f"/settings/devices/{device_id}/active", data={"active": "0"})
    client.post(f"/settings/devices/{device_id}/active", data={"active": "1"})

    log = client.get("/settings/log").text

    assert "Gerät angelegt" in log
    assert "Gerät geändert" in log
    assert "Gerät deaktiviert" in log
    assert "Gerät aktiviert" in log
    assert "Drucker A" in log
    assert "Nutzungsdauer" in log


def test_saving_without_a_change_leaves_no_log_entry(client: AppClient) -> None:
    add(client, printer_form())
    device_id = stored_devices()[0].id

    client.post(f"/settings/devices/{device_id}/edit", data=printer_form())

    assert "Gerät geändert" not in client.get("/settings/log").text


def test_app_starts_with_the_devices_routes() -> None:
    paths = {getattr(route, "path", "") for route in create_app().routes}

    assert "/settings/devices" in paths
