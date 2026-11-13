"""Attributes and article templates (issue #44, part 1). All data is invented.

Test case numbers refer to the confirmed list in the issue.
"""

import itertools
import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import (
    Article,
    ArticleTemplate,
    Attribute,
    AttributeValue,
    ChangeLogEntry,
    TemplateAttribute,
)
from tests.auth import (
    EMPLOYEE_NAME,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
    signed_in_client,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
PREFIXES = ("attribute", "template", "log.action.attribute", "log.action.template")


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


class Stored:
    def __init__(self, id: int, name: str, values: list[str]) -> None:
        self.id, self.name, self.values = id, name, values


def attributes() -> list[Stored]:
    with db() as session:
        return [
            Stored(a.id, a.name, [v.value for v in a.values_rel])
            for a in session.query(Attribute).order_by(Attribute.id)
        ]


def templates() -> list[ArticleTemplate]:
    with db() as session:
        return list(session.query(ArticleTemplate).order_by(ArticleTemplate.id))


def template_attribute_names(template_id: int) -> list[str]:
    with db() as session:
        rows = (
            session.query(Attribute.name)
            .join(TemplateAttribute, TemplateAttribute.attribute_id == Attribute.id)
            .filter(TemplateAttribute.template_id == template_id)
            .order_by(TemplateAttribute.position)
        )
        return [name for (name,) in rows]


def log_actions() -> list[str]:
    with db() as session:
        entries = session.query(ChangeLogEntry).order_by(ChangeLogEntry.id)
        return [e.action for e in entries if e.action != "setup.admin_created"]


def with_abbreviations(values: str) -> str:
    """Adds an invented abbreviation to each plain line ("rot" -> "rot = ro"); lines that
    already have one stay as they are. Keeps the older tests short."""
    lines, used = [], set()
    for line in values.splitlines():
        if "=" in line or not line.strip():
            lines.append(line)
            continue
        base = "".join(c for c in line.strip() if c.isalnum()).lower()[:2] or "x"
        abbreviation, count = base, 1
        while abbreviation in used:
            count += 1
            abbreviation = f"{base}{count}"
        used.add(abbreviation)
        lines.append(f"{line.strip()} = {abbreviation}")
    return "\n".join(lines)


def make_attribute(client: AppClient, name: str = "Farbe", values: str = "rot\nblau"):
    values = with_abbreviations(values)
    return client.post(
        "/attributes/new", data={"name": name, "values": values}, follow_redirects=False
    )


_numbers = itertools.count(100)


def template_form(client: AppClient, **overrides: str) -> dict[str, str]:
    form = {
        "number": f"V-{next(_numbers)}",
        "name": "Halter A",
        "description": "",
        "selling_price": "",
        "weight": "",
        "min_stock": "",
    }
    form.update(overrides)
    return form


def make_template(client: AppClient, **overrides: str):
    return client.post(
        "/templates/new", data=template_form(client, **overrides), follow_redirects=False
    )


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


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


# --- Test case 1: attributes with values ----------------------------------------------------


def test_new_attribute_appears_in_the_list_with_its_values(client: AppClient) -> None:
    response = make_attribute(client, values="rot\n blau \n\nrot2")

    assert response.status_code == 303
    assert [(a.name, a.values) for a in attributes()] == [("Farbe", ["rot", "blau", "rot2"])]
    html = client.get("/attributes").text
    assert "Farbe" in html
    assert "blau" in html


def test_navigation_from_the_article_list(client: AppClient) -> None:
    html = client.get("/articles").text

    assert 'href="/attributes"' in html
    assert 'href="/templates"' in html


def test_empty_attribute_list_says_so(client: AppClient) -> None:
    assert DE["attributes.empty"] in client.get("/attributes").text


# --- Test case 2: values can be added later -------------------------------------------------


def test_a_value_can_be_added_later(client: AppClient) -> None:
    make_attribute(client)
    attribute_id = attributes()[0].id

    response = client.post(
        f"/attributes/{attribute_id}/values",
        data={"value": "grün", "abbreviation": "gr"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert attributes()[0].values == ["rot", "blau", "grün"]


def test_an_attribute_can_be_used_by_several_templates(client: AppClient) -> None:
    make_attribute(client)
    attribute_id = attributes()[0].id
    make_template(client, name="Halter A", **{f"attr_{attribute_id}": "1"})
    make_template(client, name="Halter B", **{f"attr_{attribute_id}": "1"})

    assert [template_attribute_names(t.id) for t in templates()] == [["Farbe"], ["Farbe"]]


# --- Test case 3: duplicates and required fields --------------------------------------------


@pytest.mark.parametrize("second", ["Farbe", "farbe", " FARBE "])
def test_duplicate_attribute_name_is_refused(client: AppClient, second: str) -> None:
    make_attribute(client)

    response = make_attribute(client, name=second, values="grün")

    assert response.status_code == 422
    assert field_error(response.text, "name") == DE["attribute.error.taken"]
    assert [a.name for a in attributes()] == ["Farbe"]


def test_duplicate_value_is_refused_when_creating(client: AppClient) -> None:
    response = make_attribute(client, values="rot\nRot")

    assert response.status_code == 422
    assert field_error(response.text, "values") == DE["attribute.error.value_taken"]
    assert attributes() == []


def test_duplicate_value_is_refused_when_adding(client: AppClient) -> None:
    make_attribute(client)
    attribute_id = attributes()[0].id

    response = client.post(
        f"/attributes/{attribute_id}/values", data={"value": " ROT ", "abbreviation": "xx"}
    )

    assert response.status_code == 422
    assert DE["attribute.error.value_taken"] in response.text
    assert attributes()[0].values == ["rot", "blau"]


@pytest.mark.parametrize("name", ["", "   "])
def test_attribute_needs_a_name(client: AppClient, name: str) -> None:
    response = make_attribute(client, name=name)

    assert response.status_code == 422
    assert field_error(response.text, "name") == DE["attribute.error.required"]
    assert attributes() == []


def test_attribute_needs_at_least_one_value(client: AppClient) -> None:
    response = make_attribute(client, values="  \n ")

    assert response.status_code == 422
    assert field_error(response.text, "values") == DE["attribute.error.values_required"]
    assert attributes() == []


def test_too_long_attribute_values_are_refused(client: AppClient) -> None:
    assert make_attribute(client, name="N" * 51).status_code == 422
    assert make_attribute(client, values="v" * 51).status_code == 422
    assert attributes() == []


def test_an_unused_attribute_can_be_deleted_after_confirmation(client: AppClient) -> None:
    make_attribute(client)
    attribute_id = attributes()[0].id

    page = client.get(f"/attributes/{attribute_id}/delete")
    assert page.status_code == 200
    assert attributes() != []
    response = client.post(f"/attributes/{attribute_id}/delete", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert attributes() == []
    with db() as session:
        assert session.query(AttributeValue).count() == 0


def test_an_attribute_used_by_a_template_cannot_be_deleted(client: AppClient) -> None:
    make_attribute(client)
    attribute_id = attributes()[0].id
    make_template(client, **{f"attr_{attribute_id}": "1"})

    page = client.get(f"/attributes/{attribute_id}/delete")
    response = client.post(f"/attributes/{attribute_id}/delete", data={})

    assert DE["attribute.delete.blocked"] in page.text
    assert response.status_code == 409
    assert [a.name for a in attributes()] == ["Farbe"]


# --- Test case 4: templates -----------------------------------------------------------------


def test_new_template_is_in_the_template_list_but_not_in_the_article_list(
    client: AppClient,
) -> None:
    make_attribute(client)
    make_attribute(client, name="Größe", values="S\nL")
    ids = [a.id for a in attributes()]

    response = make_template(
        client,
        description="Halter für Kabel",
        selling_price="12,90",
        weight="3",
        min_stock="5",
        **{f"attr_{ids[0]}": "1", f"attr_{ids[1]}": "1"},
    )

    assert response.status_code == 303
    (stored,) = templates()
    assert (stored.name, stored.description, stored.active) == (
        "Halter A",
        "Halter für Kabel",
        True,
    )
    assert (stored.selling_price, stored.weight, stored.min_stock) == (
        Decimal("12.90"),
        Decimal("3"),
        5,
    )
    assert response.headers["location"] == f"/templates/{stored.id}"
    assert template_attribute_names(stored.id) == ["Farbe", "Größe"]
    assert "Halter A" in client.get("/templates").text
    assert "Halter A" not in client.get("/articles").text
    assert "Halter A" not in client.get("/articles?inactive=1").text


def test_template_detail_shows_defaults_and_attributes(client: AppClient) -> None:
    make_attribute(client)
    make_template(
        client, selling_price="12,90", min_stock="5", **{f"attr_{attributes()[0].id}": "1"}
    )

    html = client.get(f"/templates/{templates()[0].id}").text

    assert "Halter A" in html
    assert "12,90" in html
    assert "Farbe" in html
    assert "rot" in html


def test_empty_template_list_says_so(client: AppClient) -> None:
    assert DE["templates.empty"] in client.get("/templates").text


def test_template_can_be_changed_including_its_attributes(client: AppClient) -> None:
    make_attribute(client)
    make_attribute(client, name="Größe", values="S\nL")
    first, second = (a.id for a in attributes())
    make_template(client, **{f"attr_{first}": "1"})
    template_id = templates()[0].id

    form = template_form(client, name="Halter B", selling_price="9,5")
    form[f"attr_{second}"] = "1"
    response = client.post(f"/templates/{template_id}", data=form, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/templates/{template_id}?saved=1"
    (stored,) = templates()
    assert (stored.name, stored.selling_price) == ("Halter B", Decimal("9.50"))
    assert template_attribute_names(template_id) == ["Größe"]
    assert client.get(f"/templates/{template_id}/edit").status_code == 200


# --- Test case 5: a template is not an article ----------------------------------------------


def test_a_template_is_not_an_article(client: AppClient) -> None:
    make_template(client)
    template_id = templates()[0].id

    assert client.get(f"/articles/{template_id}").status_code == 404
    with db() as session:
        assert session.query(Article).count() == 0


# --- Test case 6: validation of the template form ------------------------------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_template_needs_a_name(client: AppClient, value: str) -> None:
    response = make_template(client, name=value)

    assert response.status_code == 422
    assert field_error(response.text, "name") == DE["article.error.required"]
    assert templates() == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("selling_price", "abc", "article.error.number"),
        ("selling_price", "-1", "article.error.negative"),
        ("weight", "0", "article.error.zero"),
        ("min_stock", "1,5", "article.error.whole"),
        ("min_stock", "2000000", "article.error.too_large"),
        ("name", "N" * 101, "article.error.too_long"),
        ("description", "D" * 1001, "article.error.too_long"),
    ],
)
def test_invalid_template_values_are_refused(
    client: AppClient, field: str, value: str, message: str
) -> None:
    response = make_template(client, **{field: value})

    assert response.status_code == 422
    assert field_error(response.text, field) == DE[message]
    assert templates() == []


def test_unknown_attribute_ids_are_ignored(client: AppClient) -> None:
    response = make_template(client, attr_999="1")

    assert response.status_code == 303
    assert template_attribute_names(templates()[0].id) == []


# --- Test case 17e: deactivate and activate -------------------------------------------------


def test_deactivated_template_leaves_the_list_and_can_come_back(client: AppClient) -> None:
    make_template(client, name="Regalwinkel")
    template_id = templates()[0].id

    client.post(f"/templates/{template_id}/deactivate", data={})

    assert templates()[0].active is False
    assert "Regalwinkel" not in client.get("/templates").text
    assert "Regalwinkel" in client.get("/templates?inactive=1").text
    client.post(f"/templates/{template_id}/activate", data={})
    assert templates()[0].active is True
    assert "Regalwinkel" in client.get("/templates").text


# --- Delete: stages 1 and 2 for templates (no variants and no calculations exist yet) ------


def test_a_template_is_deleted_only_after_confirmation(client: AppClient) -> None:
    make_attribute(client)
    make_template(client, **{f"attr_{attributes()[0].id}": "1"})
    template_id = templates()[0].id

    page = client.get(f"/templates/{template_id}/delete")
    assert page.status_code == 200
    assert "Halter A" in page.text
    assert len(templates()) == 1

    response = client.post(f"/templates/{template_id}/delete", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert templates() == []
    with db() as session:
        assert session.query(TemplateAttribute).count() == 0
    # The attribute itself stays.
    assert [a.name for a in attributes()] == ["Farbe"]


def test_unknown_ids_give_404(client: AppClient) -> None:
    assert client.get("/templates/999").status_code == 404
    assert client.get("/templates/999/edit").status_code == 404
    assert client.get("/templates/999/delete").status_code == 404
    assert client.post("/templates/999/delete", data={}).status_code == 404
    assert client.post("/templates/999/deactivate", data={}).status_code == 404
    assert (
        client.post("/attributes/999/values", data={"value": "x", "abbreviation": "x"}).status_code
        == 404
    )
    assert client.get("/attributes/999/delete").status_code == 404


# --- Test case 20: rights -------------------------------------------------------------------

EDIT_ROUTES = [
    ("get", "/attributes/new"),
    ("post", "/attributes/new"),
    ("post", "/attributes/1/values"),
    ("get", "/attributes/1/delete"),
    ("post", "/attributes/1/delete"),
    ("get", "/templates/new"),
    ("post", "/templates/new"),
    ("get", "/templates/1/edit"),
    ("post", "/templates/1"),
    ("post", "/templates/1/deactivate"),
    ("post", "/templates/1/activate"),
    ("get", "/templates/1/delete"),
    ("post", "/templates/1/delete"),
]


def test_without_login_the_pages_redirect_to_login() -> None:
    with AppClient(create_app()) as anonymous:
        for path in ("/attributes", "/templates", "/templates/new", "/attributes/new"):
            response = anonymous.get(path, follow_redirects=False)
            assert response.status_code in (302, 303, 307)
            assert (
                "/login" in response.headers["location"] or "/setup" in response.headers["location"]
            )


@pytest.mark.parametrize(("method", "path"), EDIT_ROUTES)
def test_read_only_user_cannot_change_anything(
    client: AppClient, reader: AppClient, method: str, path: str
) -> None:
    make_attribute(client)
    make_template(client)

    response = reader.post(path, data={}) if method == "post" else reader.get(path)

    assert response.status_code == 403
    assert len(attributes()) == 1
    assert len(templates()) == 1


def test_read_only_user_can_look(client: AppClient, reader: AppClient) -> None:
    make_attribute(client)
    make_template(client)

    for path in ("/attributes", "/templates", f"/templates/{templates()[0].id}"):
        assert reader.get(path).status_code == 200
    html = reader.get("/templates").text
    assert 'href="/templates/new"' not in html


def test_employee_can_create_and_change(client: AppClient, employee: AppClient) -> None:
    assert make_attribute(employee).status_code == 303
    assert make_template(employee).status_code == 303
    assert employee.post(f"/templates/{templates()[0].id}/deactivate", data={}).status_code == 200


# --- Test case 21: form token (CSRF) --------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), [r for r in EDIT_ROUTES if r[0] == "post"])
def test_post_without_form_token_is_refused(client: AppClient, method: str, path: str) -> None:
    make_attribute(client)
    make_template(client)
    client.csrf_token = None

    response = client.post(
        path, data={"name": "x", "values": "x = x", "value": "x", "abbreviation": "x"}
    )

    assert response.status_code == 403
    assert len(attributes()) == 1
    assert len(templates()) == 1
    assert templates()[0].active is True


def test_forms_carry_the_token(client: AppClient) -> None:
    make_attribute(client)
    make_template(client)
    paths = [
        "/attributes",
        "/attributes/new",
        "/templates/new",
        f"/templates/{templates()[0].id}/edit",
        f"/templates/{templates()[0].id}",
        f"/templates/{templates()[0].id}/delete",
        f"/attributes/{attributes()[0].id}/delete",
    ]
    for path in paths:
        assert 'name="csrf_token"' in client.get(path).text, path


# --- Test case 22: change log ---------------------------------------------------------------


def test_changes_are_written_to_the_change_log(client: AppClient) -> None:
    make_attribute(client)
    attribute_id = attributes()[0].id
    client.post(f"/attributes/{attribute_id}/values", data={"value": "grün", "abbreviation": "gr"})
    make_template(client)
    template_id = templates()[0].id
    client.post(
        f"/templates/{template_id}",
        data=template_form(client, selling_price="9"),
        follow_redirects=False,
    )
    client.post(f"/templates/{template_id}/deactivate", data={})
    client.post(f"/templates/{template_id}/activate", data={})
    client.post(f"/templates/{template_id}/delete", data={})
    client.post(f"/attributes/{attribute_id}/delete", data={})

    assert log_actions() == [
        "attribute.created",
        "attribute.value_added",
        "template.created",
        "template.changed",
        "template.deactivated",
        "template.activated",
        "template.deleted",
        "attribute.deleted",
    ]


def test_refused_changes_leave_no_log_entry(client: AppClient) -> None:
    make_attribute(client, name="")
    make_template(client, name="")

    assert log_actions() == []


def test_log_page_shows_the_new_actions(client: AppClient) -> None:
    make_attribute(client)
    make_template(client)

    html = client.get("/settings/log").text

    assert DE["log.action.attribute.created"] in html
    assert DE["log.action.template.created"] in html


# --- Test case 23: migration ----------------------------------------------------------------


def test_migration_keeps_existing_articles(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    engine = make_engine(path)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0007")
    with sqlite3.connect(path) as raw:
        raw.execute(
            "INSERT INTO articles (number, number_key, name, active, created_at)"
            " VALUES ('3D-1', '3d-1', 'Altbestand', 1, '2026-01-01 00:00:00')"
        )

    run_migrations(engine)

    with Session(engine) as session:
        assert [a.number for a in session.query(Article)] == ["3D-1"]
        assert session.query(ArticleTemplate).count() == 0
        assert session.query(Attribute).count() == 0


def test_migration_can_be_undone(tmp_path: Path) -> None:
    path = tmp_path / "down.db"
    engine = make_engine(path)
    run_migrations(engine)

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0007")
    with sqlite3.connect(path) as raw:
        tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master")}
    assert "article_templates" not in tables
    assert "articles" in tables
    run_migrations(engine)


# --- Test case 24: texts from the translation files -----------------------------------------


def test_all_template_texts_exist_in_both_languages() -> None:
    german = {key for key in DE if key.startswith(PREFIXES)}
    english = {key for key in EN if key.startswith(PREFIXES)}

    assert german
    assert german == english


def test_english_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_LANGUAGE", "en")
    with signed_in_client() as client:
        make_template(client, selling_price="12,5")
        html = client.get(f"/templates/{templates()[0].id}").text

    assert EN["template.field.description"] in html
    assert "12.50" in html


def test_template_pages_escape_text(client: AppClient) -> None:
    make_template(client, name="<b>fett</b>", description="<script>alert(1)</script>")

    html = client.get(f"/templates/{templates()[0].id}").text

    assert "<b>fett</b>" not in html
    assert "<script>alert(1)</script>" not in html
