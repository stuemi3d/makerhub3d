"""Printer models on the devices page (issue #174): create from Bambuddy, power table, link.

All data is invented; Bambuddy answers come from a mock (the recorded printer list has one
printer "Beispiel-Drucker" of the model "P2S"). The numbers refer to the test cases on the issue.
"""

import json

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.models import ChangeLogEntry, PrinterModel, PrinterModelPower
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_settings import (
    TEST_SECRET_KEY,
    answer_with,
    good_handler,
    save,
)
from tests.test_device_pages import add, printer_form, stored_devices

LIST = "/settings/devices"
MODELS = "/settings/devices/models"
NEW = MODELS + "/new"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def linked(client: AppClient) -> AppClient:
    """Admin client with a working Bambuddy connection (one printer of the model P2S)."""
    answer_with(client, good_handler)
    save(client)
    return client


def printers_handler(printers: list[dict]):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/v1/printers/":
            return httpx2.Response(200, json=printers)
        return good_handler(request)

    return handler


def rows(*pairs: tuple[str, str]) -> dict[str, str]:
    raw: dict[str, str] = {}
    for index, (material, value) in enumerate(pairs):
        raw[f"material_{index}"] = material
        raw[f"kwh_{index}"] = value
    return raw


def create(client: AppClient, name: str = "P2S", *pairs: tuple[str, str]):
    return client.post(NEW, data={"name": name, **rows(*pairs)})


def stored_models() -> list[tuple[str, dict[str, str]]]:
    with Session(make_engine(Settings.from_env().database_path)) as db:
        result = []
        for model in db.scalars(select(PrinterModel).order_by(PrinterModel.id)):
            powers = db.scalars(
                select(PrinterModelPower).where(PrinterModelPower.model_id == model.id)
            )
            result.append((model.name, {p.material: str(p.kwh_per_hour) for p in powers}))
        return result


def model_id(name: str = "P2S") -> int:
    with Session(make_engine(Settings.from_env().database_path)) as db:
        return db.scalars(select(PrinterModel.id).where(PrinterModel.name == name)).one()


def log_actions() -> list[str]:
    with Session(make_engine(Settings.from_env().database_path)) as db:
        actions = db.scalars(select(ChangeLogEntry.action).order_by(ChangeLogEntry.id))
        return [action for action in actions if action.startswith("printer_model.")]


# --- Create only from Bambuddy (cases 1-6) -----------------------------------------------------


def test_devices_page_has_a_table_of_printer_models(linked: AppClient) -> None:
    page = linked.get(LIST).text

    assert NEW in page
    assert "Druckermodelle" in page


def test_new_model_offers_the_models_of_bambuddy_without_free_typing(linked: AppClient) -> None:
    page = linked.get(NEW).text

    assert "<option" in page
    assert ">P2S<" in page
    assert 'type="text" name="name"' not in page
    assert 'id="name"' in page


def test_an_existing_model_is_not_offered_again(linked: AppClient) -> None:
    create(linked)

    page = linked.get(NEW).text

    assert ">P2S<" not in page


def test_without_a_connection_there_is_a_hint_and_nothing_can_be_created(
    client: AppClient,
) -> None:
    page = client.get(NEW).text

    assert "Verbindung" in page
    assert "<option" not in page
    assert create(client).status_code == 422
    assert stored_models() == []


def test_unreachable_bambuddy_gives_a_hint_and_nothing_can_be_created(linked: AppClient) -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("boom")

    answer_with(linked, down)

    page = linked.get(NEW).text

    assert "nicht erreichbar" in page
    assert "<option" not in page
    assert create(linked).status_code == 422
    assert stored_models() == []


def test_bambuddy_without_printers_gives_a_hint(linked: AppClient) -> None:
    answer_with(linked, printers_handler([]))

    page = linked.get(NEW).text

    assert "<option" not in page
    assert "Drucker in Bambuddy" in page


def test_printers_without_a_model_offer_nothing(linked: AppClient) -> None:
    answer_with(linked, printers_handler([{"id": 5, "name": "Ohne", "model": None}]))

    assert "<option" not in linked.get(NEW).text


def test_models_are_offered_once_each_ignoring_case(linked: AppClient) -> None:
    printers = [
        {"id": 1, "name": "A", "model": "P2S"},
        {"id": 2, "name": "B", "model": "p2s"},
        {"id": 3, "name": "C", "model": "H2C"},
    ]
    answer_with(linked, printers_handler(printers))

    page = linked.get(NEW).text

    assert page.count("<option") == 2


def test_a_model_bambuddy_did_not_report_is_rejected_on_the_server(linked: AppClient) -> None:
    response = create(linked, "X9", ("PLA", "0,1"))

    assert response.status_code == 422
    assert stored_models() == []


def test_a_model_cannot_be_created_twice(linked: AppClient) -> None:
    create(linked)

    response = create(linked)

    assert response.status_code == 422
    assert len(stored_models()) == 1


# --- The power table (cases 7, 8) --------------------------------------------------------------


def test_model_is_created_with_its_table(linked: AppClient) -> None:
    response = create(linked, "P2S", ("PLA", "0,10"), ("PETG", "0.13"))

    assert response.status_code in (200, 204, 303)
    assert stored_models() == [("P2S", {"PLA": "0.10", "PETG": "0.13"})]
    page = linked.get(LIST).text
    assert "P2S" in page
    assert "PLA" in page and "0,10" in page


def test_rows_can_be_changed_added_and_removed(linked: AppClient) -> None:
    create(linked, "P2S", ("PLA", "0,10"), ("PETG", "0,13"))
    edit = f"{MODELS}/{model_id()}/edit"
    page = linked.get(edit).text
    assert 'value="PLA"' in page and 'value="0,10"' in page

    # PLA changes, PETG is removed by emptying its row, TPU is new.
    linked.post(edit, data=rows(("PLA", "0,12"), ("", ""), ("TPU", "0,09")))

    assert stored_models() == [("P2S", {"PLA": "0.12", "TPU": "0.09"})]


def test_edit_shows_empty_rows_to_add_materials(linked: AppClient) -> None:
    create(linked)

    page = linked.get(f"{MODELS}/{model_id()}/edit").text

    assert 'name="material_0"' in page
    assert 'name="kwh_0"' in page


def test_invalid_rows_are_refused_and_nothing_is_saved(linked: AppClient) -> None:
    create(linked, "P2S", ("PLA", "0,10"))
    edit = f"{MODELS}/{model_id()}/edit"

    for bad in (rows(("PLA", "0")), rows(("", "0,1")), rows(("PLA", "0,1"), ("pla", "0,2"))):
        assert linked.post(edit, data=bad).status_code == 422

    assert stored_models() == [("P2S", {"PLA": "0.10"})]


def test_the_name_of_an_existing_model_cannot_be_changed(linked: AppClient) -> None:
    create(linked, "P2S", ("PLA", "0,10"))
    page = linked.get(f"{MODELS}/{model_id()}/edit").text
    linked.post(f"{MODELS}/{model_id()}/edit", data={"name": "H2C", **rows(("PLA", "0,10"))})

    assert 'name="name"' not in page
    assert [name for name, _ in stored_models()] == ["P2S"]


# --- Rights and log (cases 9, 10) --------------------------------------------------------------


@pytest.mark.parametrize("name", [EMPLOYEE_NAME, READER_NAME])
def test_only_the_admin_can_see_and_change_models(linked: AppClient, name: str) -> None:
    create(linked, "P2S", ("PLA", "0,10"))
    add_user(name, "employee" if name == EMPLOYEE_NAME else "read_only")
    edit = f"{MODELS}/{model_id()}/edit"
    with client_as(name) as other:
        other.app.state.bambuddy_transport = linked.app.state.bambuddy_transport
        assert other.get(NEW).status_code == 403
        assert other.get(edit).status_code == 403
        assert other.post(edit, data=rows(("PLA", "9"))).status_code == 403
        assert other.post(f"{MODELS}/{model_id()}/delete", data={}).status_code == 403
        assert other.post(NEW, data={"name": "P2S"}).status_code == 403
    assert stored_models() == [("P2S", {"PLA": "0.10"})]


def test_changes_are_in_the_change_log(linked: AppClient) -> None:
    create(linked, "P2S", ("PLA", "0,10"))
    edit = f"{MODELS}/{model_id()}/edit"
    linked.post(edit, data=rows(("PLA", "0,12")))
    linked.post(edit, data=rows(("PLA", "0,12")))
    linked.post(f"{MODELS}/{model_id()}/delete", data={})

    assert log_actions() == [
        "printer_model.created",
        "printer_model.updated",
        "printer_model.deleted",
    ]
    with Session(make_engine(Settings.from_env().database_path)) as db:
        details = db.scalars(
            select(ChangeLogEntry.details).where(ChangeLogEntry.action == "printer_model.updated")
        ).one()
    assert {"field": "PLA", "old": "0.10", "new": "0.12"} in json.loads(details)


# --- Delete (case 11) --------------------------------------------------------------------------


def test_a_model_without_printers_can_be_deleted(linked: AppClient) -> None:
    create(linked, "P2S", ("PLA", "0,10"))
    delete = f"{MODELS}/{model_id()}/delete"
    assert "P2S" in linked.get(delete).text

    response = linked.post(delete, data={})

    assert response.status_code in (200, 204, 303)
    assert stored_models() == []
    with Session(make_engine(Settings.from_env().database_path)) as db:
        assert db.scalars(select(PrinterModelPower)).all() == []


def test_a_model_with_printers_cannot_be_deleted(linked: AppClient) -> None:
    create(linked)
    add(linked, printer_form(printer_model_id=str(model_id())))
    page = linked.get(LIST).text
    delete = f"{MODELS}/{model_id()}/delete"

    assert delete not in page
    assert "Noch 1 Drucker" in linked.get(delete).text
    assert linked.post(delete, data={}).status_code == 409
    assert len(stored_models()) == 1


# --- Printer and model (cases 12-16) -----------------------------------------------------------


def test_printer_form_offers_the_created_models_and_it_is_optional(linked: AppClient) -> None:
    create(linked)

    page = linked.get(f"{LIST}/new?kind=printer&manual=1").text
    assert 'name="printer_model_id"' in page and ">P2S<" in page
    assert 'name="printer_model_id"' not in linked.get(f"{LIST}/new?kind=dryer").text

    add(linked, printer_form())
    assert stored_devices()[0].printer_model_id is None


def test_a_printer_is_linked_to_a_model_and_it_shows_in_the_table(linked: AppClient) -> None:
    create(linked)
    add(linked, printer_form(printer_model_id=str(model_id())))

    assert stored_devices()[0].printer_model_id == model_id()
    assert "P2S" in linked.get(LIST).text
    edit = f"{LIST}/{stored_devices()[0].id}/edit"
    assert "selected" in linked.get(edit).text


def test_an_unknown_model_id_is_refused(linked: AppClient) -> None:
    response = linked.post(f"{LIST}/new", data=printer_form(printer_model_id="999"))

    assert response.status_code == 422
    assert stored_devices() == []


def test_a_dryer_gets_no_model(linked: AppClient) -> None:
    from tests.test_device_pages import dryer_form

    create(linked)
    add(linked, dryer_form(printer_model_id=str(model_id())))

    assert stored_devices()[0].printer_model_id is None


def test_several_printers_use_the_same_table(linked: AppClient) -> None:
    create(linked, "P2S", ("PLA", "0,10"))
    add(linked, printer_form(name="A", printer_model_id=str(model_id())))
    add(linked, printer_form(name="B", printer_model_id=str(model_id())))

    linked.post(f"{MODELS}/{model_id()}/edit", data=rows(("PLA", "0,20")))

    assert [d.printer_model_id for d in stored_devices()] == [model_id(), model_id()]
    assert stored_models() == [("P2S", {"PLA": "0.20"})]


def take_over(client: AppClient):
    return client.post(
        f"{LIST}/new", data=printer_form(bambuddy_printer_id="1", printer_model_id="")
    )


def test_a_printer_from_bambuddy_is_linked_when_its_model_fits(linked: AppClient) -> None:
    answer_with(linked, printers_handler([{"id": 1, "name": "Beispiel-Drucker", "model": "P2S"}]))
    create(linked, "P2S")

    take_over(linked)

    assert stored_devices()[0].printer_model_id == model_id()


def test_a_printer_from_bambuddy_without_a_fitting_model_stays_unlinked_with_a_hint(
    linked: AppClient,
) -> None:
    take_over(linked)

    assert stored_devices()[0].printer_model_id is None
    page = linked.get(LIST).text
    assert "Modell anlegen" in page


def test_the_model_does_not_change_the_hourly_rate(linked: AppClient) -> None:
    add(linked, printer_form(maintenance_pct="0", price="1000"))
    before = linked.get(LIST).text
    create(linked, "P2S", ("PLA", "0,10"))
    device = stored_devices()[0]
    linked.post(
        f"{LIST}/{device.id}/edit",
        data=printer_form(maintenance_pct="0", price="1000", printer_model_id=str(model_id())),
    )

    assert "0,2000" in before
    assert "0,2000" in linked.get(LIST).text


def test_lookup_for_a_device_uses_the_table_of_its_model(linked: AppClient) -> None:
    from app import printer_models

    create(linked, "P2S", ("PLA", "0,10"))
    add(linked, printer_form(name="A", printer_model_id=str(model_id())))
    add(linked, printer_form(name="B"))
    first, second = stored_devices()
    with Session(make_engine(Settings.from_env().database_path)) as db:
        table = printer_models.power_table(db, first.id)
        none = printer_models.power_table(db, second.id)

    assert printer_models.lookup(table, "PLA").kwh_per_hour == printer_models.D("0.10")
    assert printer_models.lookup(none, "PLA").status == "no_table"
