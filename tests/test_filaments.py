"""Filaments, part 1: create, edit, switch off and delete (issue #235). All data is invented.

The test case numbers refer to the confirmed list in the issue. Case 12 (browser) is in
tests/browser/test_filaments.py.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import ChangeLogEntry, Filament
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as

D = Decimal
APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
LIST = "/filaments"
NEW = "/filaments/new"


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


def stored() -> list[Filament]:
    with db() as session:
        return list(session.query(Filament).order_by(Filament.id))


def log_actions() -> list[str]:
    with db() as session:
        entries = session.query(ChangeLogEntry).order_by(ChangeLogEntry.id)
        return [e.action for e in entries if e.action != "setup.admin_created"]


def form(**overrides: str) -> dict[str, str]:
    values = {
        "brand": "Bambu Lab",
        "material": "PLA",
        "subtype": "Matte",
        "color_name": "Ivory White",
        "color": "#FFFFF0",
        "standard_price": "19,90",
        "min_stock_kg": "2",
        "note": "",
    }
    values.update(overrides)
    return values


def make(client: AppClient, **overrides: str):
    return client.post(NEW, data=form(**overrides), follow_redirects=False)


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


def table_names(html: str) -> list[str]:
    """The names in the first column of the table (without the colour square)."""
    cells = re.findall(r'<td class="cell-strong">(.*?)</td>', html, re.DOTALL)
    return [re.sub(r"<svg.*?</svg>", "", cell, flags=re.DOTALL).strip() for cell in cells]


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


# --- Case 1: create ------------------------------------------------------------------------


def test_1_a_new_filament_appears_in_the_table_with_name_price_and_chip(
    client: AppClient,
) -> None:
    assert make(client).status_code == 303
    (item,) = stored()
    assert item.standard_price == D("19.90") and item.min_stock_kg == D("2")
    assert isinstance(item.standard_price, Decimal) and item.active is True
    html = client.get(LIST).text
    assert "Bambu Lab · PLA Matte · Ivory White" in html
    assert "19,90" in html and DE["article.status.active"] in html


# --- Case 2-5: validation -----------------------------------------------------------------


def test_2_only_the_material_is_required(client: AppClient) -> None:
    make(client, brand="", subtype="", color_name="", color="", standard_price="", min_stock_kg="")
    (item,) = stored()
    assert item.standard_price is None and item.min_stock_kg is None
    assert "PLA" in client.get(LIST).text


def test_3_without_material_nothing_is_saved_and_the_popup_shows_the_error(
    client: AppClient,
) -> None:
    response = client.post(NEW, data=form(material=" "), headers={"HX-Request": "true"})
    assert response.status_code == 422
    assert field_error(response.text, "material") == DE["filament.error.required"]
    assert stored() == []


def test_4_the_same_filament_twice_is_refused_even_with_other_case_and_spaces(
    client: AppClient,
) -> None:
    make(client)
    response = client.post(
        NEW,
        data=form(brand="  bambu lab ", material="pla", color_name="IVORY  white"),
    )
    assert response.status_code == 422
    assert field_error(response.text, "material") == DE["filament.error.taken"]
    assert len(stored()) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("standard_price", "abc"),
        ("standard_price", "-1"),
        ("min_stock_kg", "x"),
        ("min_stock_kg", "-2"),
    ],
)
def test_5_a_price_or_minimum_that_is_no_positive_number_is_refused(
    client: AppClient, field: str, value: str
) -> None:
    response = client.post(NEW, data=form(**{field: value}))
    assert response.status_code == 422 and field_error(response.text, field)
    assert stored() == []


def test_5_a_comma_is_read_as_decimal_point_and_empty_differs_from_zero(
    client: AppClient,
) -> None:
    make(client, standard_price="19,90", min_stock_kg="2,5")
    make(client, color_name="Black", min_stock_kg="0")
    make(client, color_name="Red", min_stock_kg="")
    first, second, third = stored()
    assert first.standard_price == D("19.90") and first.min_stock_kg == D("2.5")
    assert second.min_stock_kg == D("0") and second.min_stock_kg is not None
    assert third.min_stock_kg is None


def test_a_colour_must_look_like_a_colour_code(client: AppClient) -> None:
    response = client.post(NEW, data=form(color="red"))
    assert response.status_code == 422 and field_error(response.text, "color")
    assert stored() == []


# --- Case 6-8: edit, duplicate on edit, switch off -----------------------------------------


def test_6_editing_changes_price_and_colour_name_and_the_name_follows(client: AppClient) -> None:
    make(client)
    item_id = stored()[0].id
    response = client.post(
        f"{LIST}/{item_id}",
        data=form(standard_price="21,50", color_name="Snow"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    (item,) = stored()
    assert item.standard_price == D("21.50")
    assert table_names(client.get(LIST).text) == ["Bambu Lab · PLA Matte · Snow"]


def test_7_an_edit_that_makes_a_duplicate_is_refused_but_the_filament_itself_does_not_count(
    client: AppClient,
) -> None:
    make(client)
    make(client, color_name="Black")
    first, second = stored()
    same = client.post(f"{LIST}/{first.id}", data=form(note="nur Notiz"), follow_redirects=False)
    assert same.status_code == 303
    clash = client.post(f"{LIST}/{second.id}", data=form())
    assert clash.status_code == 422
    assert field_error(clash.text, "material") == DE["filament.error.taken"]


def test_8_switching_off_keeps_it_in_the_list_and_it_can_be_switched_on_again(
    client: AppClient,
) -> None:
    make(client)
    item_id = stored()[0].id
    client.post(f"{LIST}/{item_id}/active", data={"active": "0"}, follow_redirects=False)
    assert stored()[0].active is False
    html = client.get(LIST).text
    assert DE["article.status.inactive"] in html and "Ivory White" in html
    client.post(f"{LIST}/{item_id}/active", data={"active": "1"}, follow_redirects=False)
    assert stored()[0].active is True


# --- Case 9-10: delete ----------------------------------------------------------------------


def test_9_a_filament_with_nothing_attached_is_deleted_after_asking(client: AppClient) -> None:
    make(client)
    item_id = stored()[0].id
    question = client.get(f"{LIST}/{item_id}/delete", headers={"HX-Request": "true"}).text
    assert "Bambu Lab · PLA Matte · Ivory White" in question
    assert DE["filament.delete.button"] in question
    client.post(f"{LIST}/{item_id}/delete", follow_redirects=False)
    assert stored() == []


def test_10_the_pages_use_no_browser_dialogs(client: AppClient) -> None:
    make(client)
    item_id = stored()[0].id
    pages = [client.get(LIST).text, client.get(f"{LIST}/{item_id}/delete").text]
    for html in pages:
        assert "data-confirm" not in html and "confirm(" not in html and "alert(" not in html


# --- Case 11: table ------------------------------------------------------------------------


def test_11_the_table_sorts_by_name_brand_material_and_price(client: AppClient) -> None:
    make(client, brand="Zeta", material="PETG", color_name="A", standard_price="30")
    make(client, brand="Alpha", material="PLA", color_name="B", standard_price="10")
    make(client, brand="Mitte", material="ASA", color_name="C", standard_price="20")

    def order(sort: str, direction: str) -> list[str]:
        names = table_names(client.get(f"{LIST}?sort={sort}&dir={direction}").text)
        return [name.split(" · ")[0] for name in names]

    assert order("brand", "asc") == ["Alpha", "Mitte", "Zeta"]
    assert order("material", "asc") == ["Mitte", "Zeta", "Alpha"]
    assert order("price", "desc") == ["Zeta", "Mitte", "Alpha"]
    assert order("name", "desc") == ["Zeta", "Mitte", "Alpha"]
    html = client.get(LIST).text
    assert 'class="data-table"' in html and "row-actions" in html


def test_11_a_filament_without_price_stays_at_the_end_when_sorting_by_price(
    client: AppClient,
) -> None:
    make(client, brand="OhnePreis", standard_price="", color_name="A")
    make(client, brand="MitPreis", standard_price="10", color_name="B")
    for direction in ("asc", "desc"):
        names = table_names(client.get(f"{LIST}?sort=price&dir={direction}").text)
        assert names[-1].startswith("OhnePreis")


def test_the_menu_has_the_entry_filaments_for_everyone(
    client: AppClient, reader: AppClient
) -> None:
    for session in (client, reader):
        bar = re.search(r"<header\b.*?</header>", session.get("/").text, re.DOTALL).group(0)
        assert 'href="/filaments"' in bar and DE["nav.filaments"] in bar
    page = reader.get(LIST).text
    assert re.search(r'<a href="/filaments" aria-current="page">', page)


# --- Case 13: rights ----------------------------------------------------------------------

CHANGING = [
    ("get", "/filaments/new"),
    ("post", "/filaments/new"),
    ("get", "/filaments/1/edit"),
    ("post", "/filaments/1"),
    ("post", "/filaments/1/active"),
    ("get", "/filaments/1/delete"),
    ("post", "/filaments/1/delete"),
]


def test_13_without_login_the_pages_redirect_to_login() -> None:
    with AppClient(create_app()) as anonymous:
        for path in (LIST, NEW, "/filaments/1/edit"):
            response = anonymous.get(path, follow_redirects=False)
            assert response.status_code in (302, 303, 307)
            assert (
                "/login" in response.headers["location"] or "/setup" in response.headers["location"]
            )


def test_13_everyone_signed_in_may_read_the_list(client: AppClient, reader: AppClient) -> None:
    make(client)
    assert "Ivory White" in reader.get(LIST).text
    # ... but sees no button or symbol to change anything
    page = reader.get(LIST).text
    assert NEW not in page and "/edit" not in page and "/delete" not in page


@pytest.mark.parametrize(("method", "path"), CHANGING)
def test_13_read_only_users_cannot_change_anything(
    client: AppClient, reader: AppClient, method: str, path: str
) -> None:
    make(client)
    response = reader.post(path, data=form()) if method == "post" else reader.get(path)
    assert response.status_code == 403
    assert len(stored()) == 1 and stored()[0].active is True


def test_13_employees_may_change_filaments(client: AppClient, employee: AppClient) -> None:
    assert make(employee).status_code == 303
    item_id = stored()[0].id
    assert employee.get(f"{LIST}/{item_id}/edit").status_code == 200
    employee.post(f"{LIST}/{item_id}/active", data={"active": "0"}, follow_redirects=False)
    assert stored()[0].active is False


# --- Case 14-15: safety and change log ------------------------------------------------------


def test_14_text_with_markup_is_escaped_and_forms_need_the_token(client: AppClient) -> None:
    make(client, brand="<script>alert(1)</script>", note="<b>x</b>")
    html = client.get(LIST).text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    item_id = stored()[0].id
    edit = client.get(f"{LIST}/{item_id}/edit").text
    assert "<b>x</b>" not in edit and 'name="csrf_token"' in edit
    client.csrf_token = None  # the test client adds no token any more
    assert client.post(NEW, data=form(color_name="Other")).status_code == 403
    assert len(stored()) == 1


def test_15_create_change_switch_and_delete_are_in_the_change_log(client: AppClient) -> None:
    make(client)
    item_id = stored()[0].id
    client.post(f"{LIST}/{item_id}", data=form(standard_price="21"), follow_redirects=False)
    client.post(f"{LIST}/{item_id}/active", data={"active": "0"}, follow_redirects=False)
    client.post(f"{LIST}/{item_id}/active", data={"active": "1"}, follow_redirects=False)
    client.post(f"{LIST}/{item_id}/delete", follow_redirects=False)
    assert log_actions() == [
        "filament.created",
        "filament.changed",
        "filament.deactivated",
        "filament.activated",
        "filament.deleted",
    ]
    for action in set(log_actions()):
        assert DE[f"log.action.{action}"] and EN[f"log.action.{action}"]


def test_all_filament_texts_exist_in_german_and_english() -> None:
    german = {k for k in DE if k.startswith(("filament", "log.action.filament"))}
    english = {k for k in EN if k.startswith(("filament", "log.action.filament"))}
    assert german == english and "nav.filaments" in DE and "nav.filaments" in EN


# --- Case 16: migration ---------------------------------------------------------------------


def test_16_the_migration_creates_the_table_and_can_go_back_and_forth(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "filaments.db")
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0029")
    assert "filaments" not in inspect(engine).get_table_names()
    run_migrations(engine)
    assert "filaments" in inspect(engine).get_table_names()
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0029")
    assert "filaments" not in inspect(engine).get_table_names()
    run_migrations(engine)
    assert "filaments" in inspect(engine).get_table_names()


# --- Extra checks from the review ------------------------------------------------------------


@pytest.mark.parametrize("value", ["20", "1000,5", "0", "2,25"])
def test_a_minimum_is_shown_in_the_edit_form_as_it_was_typed(client: AppClient, value: str) -> None:
    make(client, min_stock_kg=value)
    item_id = stored()[0].id
    html = client.get(f"{LIST}/{item_id}/edit").text
    assert re.search(rf'name="min_stock_kg"[^>]*value="{re.escape(value)}"', html)


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", p) for p in ("/filaments/999/edit", "/filaments/999/delete")]
    + [("post", p) for p in ("/filaments/999", "/filaments/999/active", "/filaments/999/delete")],
)
def test_an_unknown_filament_gives_404(client: AppClient, method: str, path: str) -> None:
    response = client.post(path, data=form()) if method == "post" else client.get(path)
    assert response.status_code == 404
