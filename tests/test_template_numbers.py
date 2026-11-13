"""Template numbers, abbreviations of attribute values and the suggested variant number
(issue #62). All data is invented. Test case numbers refer to the confirmed list in the issue.
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import alembic_config, make_engine, run_migrations
from app.models import AttributeValue, ChangeLogEntry
from tests.auth import (
    EMPLOYEE_NAME,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
)
from tests.test_stock import add_article
from tests.test_templates import (
    attributes,
    db,
    field_error,
    make_template,
    template_form,
    templates,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))


def abbreviations(attribute_id: int) -> dict[str, str]:
    with db() as session:
        rows = session.query(AttributeValue).filter_by(attribute_id=attribute_id)
        return {row.value: row.abbreviation for row in rows}


def raw_attribute(client: AppClient, values: str, name: str = "Farbe"):
    return client.post(
        "/attributes/new", data={"name": name, "values": values}, follow_redirects=False
    )


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


# --- Test case 1: template number is shown and required ------------------------------------


def test_template_number_is_shown_in_list_and_detail(client: AppClient) -> None:
    make_template(client, number="123456")

    assert "123456" in client.get("/templates").text
    assert "123456" in client.get(f"/templates/{templates()[0].id}").text
    assert templates()[0].number == "123456"


@pytest.mark.parametrize("value", ["", "   "])
def test_template_without_number_is_refused(client: AppClient, value: str) -> None:
    response = make_template(client, number=value)

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["article.error.required"]
    assert templates() == []


def test_too_long_or_unprintable_number_is_refused(client: AppClient) -> None:
    assert make_template(client, number="1" * 51).status_code == 422
    assert make_template(client, number="12\t34").status_code == 422
    assert templates() == []


# --- Test case 2: the number is unique -----------------------------------------------------


@pytest.mark.parametrize("second", ["123456", " 123456 ", "AB-1", "ab-1"])
def test_number_of_another_template_is_refused(client: AppClient, second: str) -> None:
    make_template(client, number="123456")
    make_template(client, number="ab-1", name="Zweite")
    before = len(templates())

    response = make_template(client, number=second, name="Dritte")

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["template.error.number_taken"]
    assert len(templates()) == before


def test_number_of_an_article_is_refused(client: AppClient) -> None:
    add_article("3D-0001")

    response = make_template(client, number="3d-0001")

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["template.error.number_taken"]


def test_number_can_be_changed_but_not_to_a_taken_one(client: AppClient) -> None:
    make_template(client, number="111", name="Eins")
    make_template(client, number="222", name="Zwei")
    first, second = templates()

    ok = client.post(
        f"/templates/{first.id}",
        data=template_form(client, number="333", name="Eins"),
        follow_redirects=False,
    )
    same = client.post(
        f"/templates/{first.id}",
        data=template_form(client, number="333", name="Eins neu"),
        follow_redirects=False,
    )
    taken = client.post(
        f"/templates/{second.id}", data=template_form(client, number="333", name="Zwei")
    )

    assert ok.status_code == 303
    assert same.status_code == 303
    assert taken.status_code == 422
    assert field_error(taken.text, "number") == DE["template.error.number_taken"]
    assert [t.number for t in templates()] == ["333", "222"]


# --- Tests 3 + 8: abbreviation when creating an attribute ----------------------------------


def test_abbreviations_are_saved_with_the_values(client: AppClient) -> None:
    response = raw_attribute(client, "blau = bl\nrot = rt")

    assert response.status_code == 303
    assert abbreviations(attributes()[0].id) == {"blau": "bl", "rot": "rt"}


def test_abbreviations_are_shown_in_the_attribute_list(client: AppClient) -> None:
    raw_attribute(client, "blau = bl")

    assert "blau (bl)" in client.get("/attributes").text


@pytest.mark.parametrize("values", ["blau", "blau =", "blau = ", "blau\nrot = rt"])
def test_a_value_without_abbreviation_is_refused(client: AppClient, values: str) -> None:
    response = raw_attribute(client, values)

    assert response.status_code == 422
    assert field_error(response.text, "values") == DE["attribute.error.abbreviation_required"]
    assert attributes() == []


# --- Test case 4: no abbreviation twice in one attribute ------------------------------------


@pytest.mark.parametrize("values", ["blau = bl\nblass = bl", "blau = bl\nblass = BL"])
def test_the_same_abbreviation_twice_is_refused(client: AppClient, values: str) -> None:
    response = raw_attribute(client, values)

    assert response.status_code == 422
    assert field_error(response.text, "values") == DE["attribute.error.abbreviation_taken"]
    assert attributes() == []


def test_the_same_abbreviation_in_another_attribute_is_fine(client: AppClient) -> None:
    raw_attribute(client, "blau = bl", name="Farbe")
    raw_attribute(client, "breit = bl", name="Form")

    assert len(attributes()) == 2


# --- Test case 5: adding a value later -----------------------------------------------------


def test_a_value_added_later_needs_an_abbreviation(client: AppClient) -> None:
    raw_attribute(client, "blau = bl")
    attribute_id = attributes()[0].id

    missing = client.post(f"/attributes/{attribute_id}/values", data={"value": "rot"})
    taken = client.post(
        f"/attributes/{attribute_id}/values", data={"value": "rot", "abbreviation": "BL"}
    )
    ok = client.post(
        f"/attributes/{attribute_id}/values",
        data={"value": "rot", "abbreviation": "rt"},
        follow_redirects=False,
    )

    assert missing.status_code == 422
    assert DE["attribute.error.abbreviation_required"] in missing.text
    assert taken.status_code == 422
    assert DE["attribute.error.abbreviation_taken"] in taken.text
    assert ok.status_code == 303
    assert abbreviations(attribute_id) == {"blau": "bl", "rot": "rt"}


# --- Test case 8: allowed characters and length --------------------------------------------


@pytest.mark.parametrize("abbreviation", ["b-l", "b l", "b_l", "b/l", "b.l", "<b>"])
def test_abbreviation_with_other_characters_is_refused(
    client: AppClient, abbreviation: str
) -> None:
    response = raw_attribute(client, f"blau = {abbreviation}")

    assert response.status_code == 422
    assert field_error(response.text, "values") == DE["attribute.error.abbreviation_chars"]


def test_abbreviation_longer_than_ten_characters_is_refused(client: AppClient) -> None:
    assert raw_attribute(client, "blau = " + "a" * 11).status_code == 422
    assert raw_attribute(client, "blau = " + "a" * 10).status_code in (303,)


def test_abbreviation_rules_apply_when_adding_a_value(client: AppClient) -> None:
    raw_attribute(client, "blau = bl")
    attribute_id = attributes()[0].id

    response = client.post(
        f"/attributes/{attribute_id}/values", data={"value": "rot", "abbreviation": "r-t"}
    )

    assert response.status_code == 422
    assert DE["attribute.error.abbreviation_chars"] in response.text


def test_a_value_may_contain_an_equals_sign(client: AppClient) -> None:
    raw_attribute(client, "a=b = ab")

    assert abbreviations(attributes()[0].id) == {"a=b": "ab"}


# --- Tests 6 + 7: suggested variant number -------------------------------------------------


def two_attribute_template(client: AppClient) -> tuple[int, dict[str, int]]:
    raw_attribute(client, "blau = bl\nrot = rt", name="Farbe")
    raw_attribute(client, "klein = s\nmittel = m", name="Größe")
    colour, size = attributes()
    make_template(client, number="123456", **{f"attr_{colour.id}": "1", f"attr_{size.id}": "1"})
    with db() as session:
        values = {v.value: v.id for v in session.query(AttributeValue)}
    return templates()[0].id, values


def test_new_variant_form_carries_what_the_page_needs_for_the_suggestion(
    client: AppClient,
) -> None:
    template_id, values = two_attribute_template(client)

    html = client.get(f"/templates/{template_id}/variants/new").text

    assert 'data-number-prefix="123456"' in html
    assert re.search(rf'<option value="{values["blau"]}"[^>]*data-abbreviation="bl"', html)
    assert re.search(rf'<option value="{values["mittel"]}"[^>]*data-abbreviation="m"', html)


def test_a_template_without_number_gives_no_suggestion(client: AppClient) -> None:
    raw_attribute(client, "blau = bl")
    colour = attributes()[0]
    make_template(client, number="x", **{f"attr_{colour.id}": "1"})
    with db() as session:
        session.execute(text("UPDATE article_templates SET number=NULL"))
        session.commit()

    html = client.get(f"/templates/{templates()[0].id}/variants/new").text

    assert "data-number-prefix" not in html


def test_the_suggested_number_can_be_saved_or_replaced(client: AppClient) -> None:
    template_id, values = two_attribute_template(client)
    colour_id, size_id = (a.id for a in attributes())

    def post(number: str):
        return client.post(
            f"/templates/{template_id}/variants/new",
            data={
                "number": number,
                "name": number,
                "selling_price": "",
                "weight": "",
                "min_stock": "",
                f"attr_{colour_id}": str(values["blau"]),
                f"attr_{size_id}": str(values["mittel"]),
            },
            follow_redirects=False,
        )

    assert post("123456-bl-m").status_code == 303
    second = post("123456-bl-m")
    assert second.status_code == 422
    assert field_error(second.text, "number") == DE["article.error.taken"]


# --- Test case 9: migration ----------------------------------------------------------------


def migrate_old_database(tmp_path: Path, values: list[str]) -> Session:
    path = tmp_path / "old.db"
    engine = make_engine(path)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0010")
    with sqlite3.connect(path) as raw:
        raw.execute("INSERT INTO attributes (id, name, name_key) VALUES (1, 'Farbe', 'farbe')")
        raw.execute("INSERT INTO attributes (id, name, name_key) VALUES (2, 'Form', 'form')")
        for value in values:
            raw.execute(
                "INSERT INTO attribute_values (attribute_id, value, value_key) VALUES (1, ?, ?)",
                (value, value.casefold()),
            )
        raw.execute(
            "INSERT INTO attribute_values (attribute_id, value, value_key)"
            " VALUES (2, 'Blass', 'blass')"
        )
        raw.execute(
            "INSERT INTO article_templates (name, description, active, created_at)"
            " VALUES ('Alt', '', 1, '2026-01-01 00:00:00')"
        )
    run_migrations(engine)
    return Session(engine)


def test_migration_suggests_abbreviations_and_keeps_templates(tmp_path: Path) -> None:
    with migrate_old_database(tmp_path, ["blau", "Blass", "rot", "Größe 4", "x"]) as session:
        suggested = {
            v.value: v.abbreviation for v in session.query(AttributeValue).filter_by(attribute_id=1)
        }
        other = session.query(AttributeValue).filter_by(attribute_id=2).one()

    assert suggested == {
        "blau": "bl",
        "Blass": "bl2",
        "rot": "ro",
        "Größe 4": "gr",
        "x": "x",
    }
    # Another attribute starts its own numbering.
    assert other.abbreviation == "bl"


def test_migrated_template_without_number_shows_a_hint(tmp_path: Path) -> None:
    with migrate_old_database(tmp_path, ["blau"]) as session:
        number = session.execute(text("SELECT number FROM article_templates")).scalar()
    assert number is None


def test_migration_can_be_undone(tmp_path: Path) -> None:
    path = tmp_path / "down.db"
    engine = make_engine(path)
    run_migrations(engine)

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0010")
    with sqlite3.connect(path) as raw:
        columns = {row[1] for row in raw.execute("PRAGMA table_info(article_templates)")}
    assert "number" not in columns
    run_migrations(engine)


def test_template_without_number_shows_a_hint_to_add_one(client: AppClient) -> None:
    make_template(client)
    with db() as session:
        session.execute(text("UPDATE article_templates SET number=NULL"))
        session.commit()
    template_id = templates()[0].id

    detail = client.get(f"/templates/{template_id}").text
    form = client.post(
        f"/templates/{template_id}", data=template_form(client, number=""), follow_redirects=False
    )

    assert DE["template.number_missing"] in detail
    # Saving an old template without a number is still possible (the hint stays).
    assert form.status_code == 303
    assert templates()[0].number is None
    # With a number it can be added.
    client.post(f"/templates/{template_id}", data=template_form(client, number="NEU-1"))
    assert templates()[0].number == "NEU-1"


# --- Test case 10: rights, form token, log, texts -------------------------------------------


def test_read_only_user_cannot_add_numbers_or_abbreviations(
    client: AppClient, reader: AppClient
) -> None:
    raw_attribute(client, "blau = bl")
    attribute_id = attributes()[0].id

    assert (
        reader.post(
            f"/attributes/{attribute_id}/values", data={"value": "x", "abbreviation": "x"}
        ).status_code
        == 403
    )
    assert reader.post("/attributes/new", data={"name": "x", "values": "x = x"}).status_code == 403
    assert reader.post("/templates/new", data=template_form(reader)).status_code == 403
    assert templates() == []
    assert abbreviations(attribute_id) == {"blau": "bl"}


def test_requests_without_form_token_are_refused(client: AppClient) -> None:
    client.csrf_token = None

    assert client.post("/templates/new", data=template_form(client)).status_code == 403
    assert templates() == []


def test_forms_have_the_new_fields(client: AppClient) -> None:
    raw_attribute(client, "blau = bl")
    attribute_id = attributes()[0].id

    assert 'name="number"' in client.get("/templates/new").text
    assert 'name="abbreviation"' in client.get("/attributes").text
    assert f'id="abbreviation-{attribute_id}"' in client.get("/attributes").text


def test_employee_can_add_numbers(client: AppClient, employee: AppClient) -> None:
    assert make_template(employee, number="E-1").status_code == 303


def test_numbers_and_abbreviations_are_logged(client: AppClient) -> None:
    raw_attribute(client, "blau = bl")
    attribute_id = attributes()[0].id
    client.post(f"/attributes/{attribute_id}/values", data={"value": "rot", "abbreviation": "rt"})
    make_template(client, number="111")
    client.post(
        f"/templates/{templates()[0].id}",
        data=template_form(client, number="222"),
    )

    with db() as session:
        entries = {e.action: e for e in session.query(ChangeLogEntry)}
    added = json.dumps(json.loads(entries["attribute.value_added"].details or "[]"))
    changed = json.dumps(json.loads(entries["template.changed"].details or "[]"))
    assert "rt" in added
    assert "111" in changed and "222" in changed
    html = client.get("/settings/log").text
    assert DE["article.field.abbreviation"] in html


def test_new_texts_exist_in_both_languages() -> None:
    prefixes = ("template.error.number", "template.number", "attribute.error.abbreviation")
    german = {k for k in DE if k.startswith(prefixes)}
    english = {k for k in EN if k.startswith(prefixes)}

    assert german
    assert german == english
    for key in ("article.field.abbreviation", "attribute.field.abbreviation"):
        assert key in DE and key in EN
