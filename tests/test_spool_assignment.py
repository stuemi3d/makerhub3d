"""Filaments, part 2: assign Bambuddy spools to filaments (issue #236). All data is invented.

The test case numbers refer to the confirmed list in the issue. Case 19 (browser) is in
tests/browser/test_spool_assignment.py. Bambuddy answers come from a mock.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import ChangeLogEntry, SpoolAssignment
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_settings import TEST_SECRET_KEY, answer_with, good_handler, save
from tests.test_filaments import LIST, make, stored, table_names

D = Decimal
APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
PAGE = "/filaments/spools"
ASSIGN = "/filaments/spools/assign"
NAME = "Bambu Lab · PLA Matte · Ivory White"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


def assignments() -> list[SpoolAssignment]:
    with db() as session:
        return list(session.query(SpoolAssignment).order_by(SpoolAssignment.spool_id))


def spool(spool_id: int, **more) -> dict:
    item = {
        "id": spool_id,
        "material": "PLA",
        "brand": "Bambu Lab",
        "subtype": "Matte",
        "color_name": "Ivory White",
        "rgba": "FFFFF0FF",
        "cost_per_kg": 19.9,
        "label_weight": 1000,
        "weight_used": 100,
        "archived_at": None,
    }
    item.update(more)
    return item


class Bambuddy:
    """What the mock Bambuddy says; remembers the requests."""

    def __init__(self, spools: list[dict]) -> None:
        self.spools = spools
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.path == "/api/v1/inventory/spools":
            with_archived = request.url.params.get("include_archived") == "true"
            return httpx2.Response(
                200, json=[s for s in self.spools if with_archived or not s["archived_at"]]
            )
        return good_handler(request)


def linked(client: AppClient, *spools: dict) -> Bambuddy:
    """Admin client with a working Bambuddy connection that has these spools."""
    bambuddy = Bambuddy(list(spools))
    answer_with(client, good_handler)
    save(client)
    answer_with(client, bambuddy)
    return bambuddy


def rows(html: str) -> dict[int, str]:
    """The table rows of the page by spool number."""
    pattern = r'<tr id="spool-(\d+)".*?</tr>'
    return {int(m.group(1)): m.group(0) for m in re.finditer(pattern, html, re.DOTALL)}


def selected(html: str, spool_id: int) -> str | None:
    """The value of the selected option of the choice of one spool."""
    select = re.search(rf'<select[^>]*name="choice_{spool_id}".*?</select>', html, re.DOTALL)
    assert select, f"no choice for spool {spool_id}"
    option = re.search(r'<option value="([^"]*)" selected', select.group(0))
    return option.group(1) if option else None


def post(client: AppClient, scope: str, **fields: str):
    return client.post(ASSIGN, data={"scope": scope, **fields}, follow_redirects=False)


@pytest.fixture
def employee(client: AppClient) -> AppClient:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as test_client:
        yield test_client


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


# --- Case 1: the list -----------------------------------------------------------------------


def test_1_the_list_shows_active_and_archived_spools_without_filament(client: AppClient) -> None:
    linked(client, spool(1), spool(2, archived_at="2026-11-01T10:00:00"), spool(3))
    post(client, "3", choice_3="new")
    html = client.get(PAGE).text
    assert set(rows(html)) == {1, 2}
    assert DE["filaments.spool.in_archive"] in rows(html)[2]
    assert DE["filaments.spool.in_archive"] not in rows(html)[1]


# --- Case 2-5: proposals ---------------------------------------------------------------------


def test_2_an_existing_filament_with_the_same_details_is_preselected(client: AppClient) -> None:
    linked(client, spool(1, brand=" bambu  LAB ", color_name="IVORY white"))
    make(client)
    (item,) = stored()
    assert selected(client.get(PAGE).text, 1) == f"f{item.id}"


def test_3_without_a_match_a_new_filament_is_proposed_and_created_on_confirming(
    client: AppClient,
) -> None:
    linked(client, spool(1))
    html = client.get(PAGE).text
    assert selected(html, 1) == "new"
    assert NAME in rows(html)[1]
    assert stored() == []
    post(client, "1", choice_1="new")
    (item,) = stored()
    assert item.brand == "Bambu Lab" and item.material == "PLA"
    assert item.subtype == "Matte" and item.color_name == "Ivory White"
    assert item.color == "#FFFFF0" and item.standard_price is None and item.active


def test_4_a_spool_without_colour_name_gets_a_field_and_no_guessed_name(
    client: AppClient,
) -> None:
    linked(client, spool(1, color_name=None, rgba="112233FF"))
    html = client.get(PAGE).text
    assert 'name="color_name_1"' in rows(html)[1]
    assert "#112233" in rows(html)[1]
    post(client, "1", choice_1="new", color_name_1="Nachtblau")
    (item,) = stored()
    assert item.color_name == "Nachtblau" and item.color == "#112233"


def test_4_the_colour_name_may_stay_empty(client: AppClient) -> None:
    linked(client, spool(1, color_name=None))
    post(client, "1", choice_1="new", color_name_1="")
    (item,) = stored()
    assert item.color_name == ""


def test_5_the_next_spool_with_exactly_the_same_details_gets_the_same_filament(
    client: AppClient,
) -> None:
    linked(client, spool(1, brand="Bambu"), spool(2, brand="Bambu"))
    make(client)
    (item,) = stored()
    post(client, "1", choice_1=f"f{item.id}")
    html = client.get(PAGE).text
    assert selected(html, 2) == f"f{item.id}"


def test_5_similar_spellings_are_never_joined_by_themselves(client: AppClient) -> None:
    linked(client, spool(1, color_name="Grey"))
    make(client, color_name="Gray", brand="Bambu Lab", subtype="Matte")
    assert selected(client.get(PAGE).text, 1) == "new"


# --- Case 6-8: confirming ----------------------------------------------------------------------


def test_6_one_row_is_confirmed_and_leaves_the_list(client: AppClient) -> None:
    linked(client, spool(1), spool(2, color_name="Black"))
    response = post(client, "1", choice_1="new", choice_2="new")
    assert response.status_code == 303
    assert [a.spool_id for a in assignments()] == [1]
    assert set(rows(client.get(PAGE).text)) == {2}


def test_7_confirm_all_assigns_every_row_and_creates_a_shared_filament_once(
    client: AppClient,
) -> None:
    linked(client, spool(1), spool(2), spool(3, color_name="Black"))
    post(client, "all", choice_1="new", choice_2="new", choice_3="new")
    assert [a.spool_id for a in assignments()] == [1, 2, 3]
    assert len(stored()) == 2
    first, second, third = assignments()
    assert first.filament_id == second.filament_id != third.filament_id


def test_8_a_changed_choice_or_no_assignment_is_respected(client: AppClient) -> None:
    linked(client, spool(1), spool(2, color_name="Black"))
    make(client, brand="Andere", material="PETG", subtype="", color_name="Rot")
    (item,) = stored()
    post(client, "all", choice_1=f"f{item.id}", choice_2="")
    (only,) = assignments()
    assert only.spool_id == 1 and only.filament_id == item.id
    assert set(rows(client.get(PAGE).text)) == {2}


# --- Case 9-11: values and rules ---------------------------------------------------------------


def test_9_price_and_nominal_weight_are_stored_as_values(client: AppClient) -> None:
    linked(client, spool(1, cost_per_kg=19.9, label_weight=750))
    post(client, "1", choice_1="new")
    (item,) = assignments()
    assert item.price_per_kg == D("19.9") and item.weight_g == D("750")
    assert isinstance(item.price_per_kg, Decimal) and isinstance(item.weight_g, Decimal)
    assert item.assigned_at is not None


def test_9_no_price_is_stored_as_no_price_but_a_price_of_0_as_0(client: AppClient) -> None:
    linked(
        client,
        spool(1, cost_per_kg=None),
        spool(2, cost_per_kg=0.0, color_name="Black"),
    )
    post(client, "all", choice_1="new", choice_2="new")
    first, second = assignments()
    assert first.price_per_kg is None
    assert second.price_per_kg == D("0") and second.price_per_kg is not None


def test_10_a_spool_belongs_to_one_filament_only(client: AppClient) -> None:
    linked(client, spool(1))
    post(client, "1", choice_1="new")
    again = post(client, "1", choice_1="new")
    assert again.status_code in (303, 422)
    assert len(assignments()) == 1 and len(stored()) == 1


def test_11_a_filament_with_spools_cannot_be_deleted_only_switched_off(
    client: AppClient,
) -> None:
    linked(client, spool(1))
    post(client, "1", choice_1="new")
    item_id = stored()[0].id
    question = client.get(f"{LIST}/{item_id}/delete", headers={"HX-Request": "true"}).text
    assert DE["filament.delete.blocked"] in question
    assert DE["filament.delete.button"] not in question
    assert client.post(f"{LIST}/{item_id}/delete").status_code == 409
    assert len(stored()) == 1
    client.post(f"{LIST}/{item_id}/active", data={"active": "0"}, follow_redirects=False)
    assert stored()[0].active is False


# --- Case 12-14: Bambuddy ----------------------------------------------------------------------


def test_12_bambuddy_is_only_read(client: AppClient) -> None:
    bambuddy = linked(client, spool(1))
    client.get(PAGE)
    post(client, "all", choice_1="new")
    client.get(LIST)
    assert bambuddy.requests and {r.method for r in bambuddy.requests} == {"GET"}


def test_12_the_archived_spools_are_asked_for(client: AppClient) -> None:
    bambuddy = linked(client, spool(1))
    client.get(PAGE)
    assert any(r.url.params.get("include_archived") == "true" for r in bambuddy.requests)


def test_13_without_a_working_bambuddy_the_page_says_so_and_the_list_still_works(
    client: AppClient,
) -> None:
    make(client)
    page = client.get(PAGE)
    assert page.status_code == 200 and DE["filaments.spools.not_set_up"] in page.text
    answer_with(client, lambda request: httpx2.Response(500))
    assert client.get(PAGE).status_code == 200
    assert post(client, "all", choice_1="new").status_code in (303, 422)
    assert assignments() == []
    listing = client.get(LIST)
    assert listing.status_code == 200 and table_names(listing.text) == [NAME]
    assert "data-open-spools" not in listing.text


def test_14_the_button_shows_how_many_spools_wait(client: AppClient) -> None:
    linked(client, spool(1), spool(2, color_name="Black"))
    html = client.get(LIST).text
    assert DE["filaments.assign"] in html and 'href="/filaments/spools"' in html
    assert re.search(r"data-open-spools[^>]*>2<", html)
    post(client, "all", choice_1="new", choice_2="new")
    html = client.get(LIST).text
    assert DE["filaments.assign"] in html and "data-open-spools" not in html


# --- Case 15-17: rights, safety, log ------------------------------------------------------------


def test_15_without_login_the_pages_redirect_to_login() -> None:
    with AppClient(create_app()) as anonymous:
        for path in (PAGE,):
            response = anonymous.get(path, follow_redirects=False)
            assert response.status_code in (302, 303, 307)
            assert (
                "/login" in response.headers["location"] or "/setup" in response.headers["location"]
            )


def test_15_employees_may_assign_and_readers_may_not(
    client: AppClient, employee: AppClient, reader: AppClient
) -> None:
    answer_with(employee, linked(client, spool(1)))
    assert reader.get(PAGE).status_code == 403
    assert reader.post(ASSIGN, data={"scope": "all", "choice_1": "new"}).status_code == 403
    assert assignments() == []
    assert employee.post(ASSIGN, data={"scope": "all", "choice_1": "new"}).status_code in (200, 303)
    assert len(assignments()) == 1


def test_15_the_button_is_hidden_from_readers(client: AppClient, reader: AppClient) -> None:
    linked(client, spool(1))
    assert 'href="/filaments/spools"' not in reader.get(LIST).text


def test_16_a_spool_name_with_markup_is_escaped_and_forms_need_the_token(
    client: AppClient,
) -> None:
    linked(client, spool(1, brand="<script>alert(1)</script>"))
    html = client.get(PAGE).text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert 'name="csrf_token"' in html
    client.csrf_token = None
    assert client.post(ASSIGN, data={"scope": "all", "choice_1": "new"}).status_code == 403
    assert assignments() == []


def test_16_invented_numbers_are_ignored(client: AppClient) -> None:
    linked(client, spool(1))
    make(client)
    post(client, "all", choice_1="f99999", choice_2="new", choice_999="new")
    assert assignments() == []
    post(client, "999", choice_999="new")
    assert assignments() == []
    assert len(stored()) == 1


def test_16_the_values_come_from_bambuddy_not_from_the_form(client: AppClient) -> None:
    linked(client, spool(1, cost_per_kg=19.9))
    post(client, "1", choice_1="new", price_per_kg_1="1", cost_per_kg="1", weight_g="5")
    (item,) = assignments()
    assert item.price_per_kg == D("19.9") and item.weight_g == D("1000")


def test_17_every_assignment_is_in_the_change_log(client: AppClient) -> None:
    linked(client, spool(1), spool(2))
    post(client, "all", choice_1="new", choice_2="new")
    with db() as session:
        entries = [
            e
            for e in session.query(ChangeLogEntry).order_by(ChangeLogEntry.id)
            if e.action.startswith("filament.")
        ]
    assert [e.action for e in entries] == [
        "filament.created",
        "filament.spool_assigned",
        "filament.spool_assigned",
    ]
    assert NAME in entries[1].subject and "#1" in entries[1].details
    assert DE["log.action.filament.spool_assigned"] and EN["log.action.filament.spool_assigned"]


def test_all_new_texts_exist_in_german_and_english() -> None:
    german = {k for k in DE if k.startswith(("filament", "log.action.filament"))}
    english = {k for k in EN if k.startswith(("filament", "log.action.filament"))}
    assert german == english


# --- Case 18: migration -------------------------------------------------------------------------


def test_18_the_migration_creates_the_table_and_can_go_back_and_forth(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "assignments.db")
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0030")
    assert "spool_assignments" not in inspect(engine).get_table_names()
    run_migrations(engine)
    assert "spool_assignments" in inspect(engine).get_table_names()
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0030")
    assert "spool_assignments" not in inspect(engine).get_table_names()
    assert "filaments" in inspect(engine).get_table_names()
    run_migrations(engine)
    assert "spool_assignments" in inspect(engine).get_table_names()


# --- Case 20: no browser dialogs ----------------------------------------------------------------


def test_20_the_page_uses_the_shared_table_and_no_browser_dialogs(client: AppClient) -> None:
    linked(client, spool(1))
    html = client.get(PAGE).text
    assert 'class="data-table"' in html and "row-actions" in html
    assert "data-confirm" not in html and "confirm(" not in html and "alert(" not in html


# --- The client reads what the page needs ---------------------------------------------------------


def test_the_client_reads_subtype_and_colour_of_a_spool() -> None:
    from app.bambuddy import BambuddyClient

    bambuddy = Bambuddy([spool(1), spool(2, subtype=None, rgba=None)])
    client = BambuddyClient(
        "http://bambuddy.example", "key", transport=httpx2.MockTransport(bambuddy)
    )
    first, second = client.spools()
    assert first.subtype == "Matte" and first.color == "#FFFFF0"
    assert second.subtype is None and second.color is None


# --- Issue #236, follow-up after the test of the pull request ---------------------------------

SAME_PETG = dict(material="PETG", color_name="Gray")


def test_21_identical_spools_without_a_filament_are_marked_as_belonging_together(
    client: AppClient,
) -> None:
    linked(client, *[spool(n, **SAME_PETG) for n in (50, 51, 52, 53, 54)])
    html = client.get(PAGE).text
    first = rows(html)[50]
    assert DE["filaments.spool.new"] in first and "wie #50" not in first
    for number in (51, 52, 53, 54):
        assert "wie #50" in rows(html)[number]
    post(client, "all", **{f"choice_{n}": "new" for n in (50, 51, 52, 53, 54)})
    (item,) = stored()
    assert [a.filament_id for a in assignments()] == [item.id] * 5


def test_22_spools_with_different_details_get_no_such_note(client: AppClient) -> None:
    linked(client, spool(1), spool(2, color_name="Black"))
    html = client.get(PAGE).text
    assert "wie #" not in html


def test_23_a_similar_brand_is_proposed_with_a_note_and_assigned_only_on_confirming(
    client: AppClient,
) -> None:
    linked(client, spool(3, brand="Bambu", archived_at="2026-11-01T10:00:00"))
    make(client)
    (item,) = stored()
    html = client.get(PAGE).text
    assert selected(html, 3) == f"f{item.id}"
    assert "Bambu ↔ Bambu Lab" in rows(html)[3]
    assert assignments() == []
    post(client, "3", choice_3=f"f{item.id}")
    assert [a.filament_id for a in assignments()] == [item.id]


@pytest.mark.parametrize(
    "more",
    [
        dict(brand="Polymaker"),  # another brand
        dict(brand="Bambu Labor"),  # not at the start of a word
        dict(color_name="Ivory Whyte"),  # another colour
        dict(subtype="Basic"),  # another subtype
        dict(material="PETG"),  # another material
        dict(brand=None),  # no brand
    ],
)
def test_24_only_a_brand_at_a_word_start_with_the_same_rest_is_proposed(
    client: AppClient, more: dict
) -> None:
    linked(client, spool(3, **{"brand": "Bambu", **more}))
    make(client)
    html = client.get(PAGE).text
    assert selected(html, 3) == "new" and "↔" not in html


def test_25_two_fitting_filaments_give_no_proposal(client: AppClient) -> None:
    linked(client, spool(3, brand="Bambu"))
    make(client, brand="Bambu Lab")
    make(client, brand="Bambu Studio")
    html = client.get(PAGE).text
    assert selected(html, 3) == "new" and "↔" not in html


def test_26_the_exact_match_has_no_note_and_an_earlier_assignment_goes_first(
    client: AppClient,
) -> None:
    linked(client, spool(1), spool(2, brand="Bambu"), spool(3, brand="Bambu"))
    make(client)
    make(client, brand="Bambu Studio")
    exact, studio = stored()
    assert "↔" not in rows(client.get(PAGE).text)[1]
    assert selected(client.get(PAGE).text, 1) == f"f{exact.id}"
    # two filaments fit "Bambu"; after one was chosen by hand, the same spools follow it
    post(client, "2", choice_2=f"f{studio.id}")
    html = client.get(PAGE).text
    assert selected(html, 3) == f"f{studio.id}" and "↔" not in rows(html)[3]


def test_27_archived_and_active_spools_are_treated_alike(client: AppClient) -> None:
    linked(
        client, spool(1, brand="Bambu"), spool(2, brand="Bambu", archived_at="2026-11-01T10:00:00")
    )
    make(client)
    (item,) = stored()
    html = client.get(PAGE).text
    assert selected(html, 1) == selected(html, 2) == f"f{item.id}"
    assert "↔" in rows(html)[1] and "↔" in rows(html)[2]


def test_the_related_brand_also_works_the_other_way_and_ignores_case_and_spaces(
    client: AppClient,
) -> None:
    linked(client, spool(1, brand="  BAMBU   lab "), spool(2, brand="bambu"))
    make(client, brand="Bambu")
    make(client, brand="Bambu  Lab", color_name="Black")
    first, _second = stored()
    html = client.get(PAGE).text
    assert selected(html, 2) == f"f{first.id}"  # "bambu" is the same brand as "Bambu"
    assert "↔" not in rows(html)[2]
    assert selected(html, 1) == f"f{first.id}" and "↔" in rows(html)[1]  # "Bambu lab" ~ "Bambu"


def test_a_proposal_with_a_brand_note_has_no_like_note_and_the_note_is_escaped(
    client: AppClient,
) -> None:
    linked(client, spool(1, brand="Bambu"), spool(2, brand="Bambu"))
    make(client, brand="Bambu <b>Lab</b>")
    html = client.get(PAGE).text
    assert "wie #" not in html and "<b>Lab</b>" not in html and "&lt;b&gt;" in html
