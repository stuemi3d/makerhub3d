"""Variants, inheritance and deleting articles (issue #49). All data is invented.

Test case numbers refer to the confirmed list in issues #44 and #49.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy.orm import Session

from app import article_purge
from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import (
    Article,
    ArticleAttributeValue,
    ArticleTemplate,
    AttributeValue,
    ChangeLogEntry,
)
from tests.auth import (
    EMPLOYEE_NAME,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
    signed_in_client,
)
from tests.test_stock import add_article, add_movement
from tests.test_templates import (
    attributes,
    field_error,
    make_attribute,
    make_template,
    template_attribute_names,
    templates,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
PREFIXES = ("variant", "article.delete", "template.error", "log.action.article")


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


@dataclass
class Setup:
    """A template "Halter A" with colour (red, blue) and size (M, L)."""

    template_id: int
    colour_id: int
    size_id: int
    values: dict[str, int]  # value text -> value id


def make_setup(client: AppClient, **template_overrides: str) -> Setup:
    make_attribute(client, "Farbe", "rot\nblau")
    make_attribute(client, "Größe", "M\nL")
    colour, size = attributes()
    form = {"selling_price": "10,00", "weight": "2", "min_stock": "5"}
    form.update(template_overrides)
    make_template(client, **{f"attr_{colour.id}": "1", f"attr_{size.id}": "1"}, **form)
    with db() as session:
        values = {v.value: v.id for v in session.query(AttributeValue)}
    return Setup(templates()[0].id, colour.id, size.id, values)


def variant_form(setup: Setup, colour: str | None = "rot", size: str | None = "L", **overrides):
    form = {
        "number": "3D-0042-RT",
        "name": "Halter A rot L",
        "selling_price": "",
        "weight": "",
        "min_stock": "",
    }
    if colour is not None:
        form[f"attr_{setup.colour_id}"] = str(setup.values[colour])
    if size is not None:
        form[f"attr_{setup.size_id}"] = str(setup.values[size])
    form.update(overrides)
    return form


def make_variant(client: AppClient, setup: Setup, **kwargs):
    return client.post(
        f"/templates/{setup.template_id}/variants/new",
        data=variant_form(setup, **kwargs),
        follow_redirects=False,
    )


def article_by_number(number: str) -> Article:
    with db() as session:
        article = session.query(Article).filter_by(number=number).one()
        session.expunge(article)
        return article


def article_count() -> int:
    with db() as session:
        return session.query(Article).count()


def value_texts(article_id: int) -> dict[str, str]:
    with db() as session:
        rows = session.query(ArticleAttributeValue).filter_by(article_id=article_id)
        return {r.attribute.name: r.value.value for r in rows}


def log_entries(action: str) -> list[ChangeLogEntry]:
    with db() as session:
        return list(session.query(ChangeLogEntry).filter_by(action=action))


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


@pytest.fixture
def setup(client: AppClient) -> Setup:
    return make_setup(client)


# --- Test case 7: create a variant ----------------------------------------------------------


def test_variant_becomes_a_full_article_pointing_to_the_template(
    client: AppClient, setup: Setup
) -> None:
    response = make_variant(client, setup)

    assert response.status_code == 303
    article = article_by_number("3D-0042-RT")
    assert response.headers["location"] == f"/articles/{article.id}"
    assert article.template_id == setup.template_id
    assert article.active is True
    assert value_texts(article.id) == {"Farbe": "rot", "Größe": "L"}
    # Own stock and own movements, like any article
    add_movement(article.id, 7)
    assert "3D-0042-RT" in client.get("/stock").text
    assert "3D-0042-RT" in client.get("/articles").text
    assert "Halter A" not in client.get("/articles?inactive=1").text.split("<tbody>")[0]


def test_form_lists_the_values_of_the_template(client: AppClient, setup: Setup) -> None:
    html = client.get(f"/templates/{setup.template_id}/variants/new").text

    for text in ("Farbe", "Größe", "rot", "blau", "M", "L"):
        assert text in html
    assert 'name="csrf_token"' in html


def test_variant_may_have_own_values_from_the_start(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup, selling_price="12,50", weight="3", min_stock="2")

    article = article_by_number("3D-0042-RT")
    assert (str(article.selling_price), str(article.weight), article.min_stock) == (
        "12.50",
        "3",
        2,
    )


def test_variant_values_are_checked_like_for_articles(client: AppClient, setup: Setup) -> None:
    response = make_variant(client, setup, selling_price="abc", number="", name="")

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["article.error.required"]
    assert field_error(response.text, "name") == DE["article.error.required"]
    assert field_error(response.text, "selling_price") == DE["article.error.number"]
    assert article_count() == 0


# --- Test case 8: duplicates ----------------------------------------------------------------


def test_same_combination_twice_is_refused(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)

    response = make_variant(client, setup, number="3D-0042-RT2", name="Zweite")

    assert response.status_code == 422
    assert DE["variant.error.combination_taken"] in response.text
    assert article_count() == 1


@pytest.mark.parametrize("number", ["3D-0042-RT", "3d-0042-rt"])
def test_taken_article_number_is_refused(client: AppClient, setup: Setup, number: str) -> None:
    make_variant(client, setup)

    response = make_variant(client, setup, colour="blau", number=number)

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["article.error.taken"]
    assert article_count() == 1


def test_article_number_of_a_normal_article_is_taken_too(client: AppClient, setup: Setup) -> None:
    client.post(
        "/articles/new",
        data={
            "number": "3D-0042-RT",
            "name": "Alt",
            "selling_price": "",
            "weight": "",
            "min_stock": "",
        },
    )

    response = make_variant(client, setup)

    assert response.status_code == 422
    assert article_count() == 1


def test_a_different_combination_is_fine(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)

    response = make_variant(client, setup, colour="blau", number="3D-0042-BL")

    assert response.status_code == 303
    assert article_count() == 2


# --- Test case 9: missing or invalid values -------------------------------------------------


def test_missing_value_is_refused(client: AppClient, setup: Setup) -> None:
    response = make_variant(client, setup, size=None)

    assert response.status_code == 422
    assert DE["variant.error.value_required"] in response.text
    assert article_count() == 0


def test_value_of_another_attribute_is_refused(client: AppClient, setup: Setup) -> None:
    form = variant_form(setup)
    form[f"attr_{setup.colour_id}"] = str(setup.values["L"])  # a size, not a colour

    response = client.post(f"/templates/{setup.template_id}/variants/new", data=form)

    assert response.status_code == 422
    assert DE["variant.error.value_invalid"] in response.text
    assert article_count() == 0


@pytest.mark.parametrize("bad", ["", "abc", "99999", "-1"])
def test_unknown_value_is_refused(client: AppClient, setup: Setup, bad: str) -> None:
    form = variant_form(setup)
    form[f"attr_{setup.size_id}"] = bad

    response = client.post(f"/templates/{setup.template_id}/variants/new", data=form)

    assert response.status_code == 422
    assert article_count() == 0


def test_template_without_attributes_has_no_variants(client: AppClient) -> None:
    make_template(client, name="Leer")
    template_id = templates()[0].id

    assert client.get(f"/templates/{template_id}/variants/new").status_code == 409
    assert client.post(f"/templates/{template_id}/variants/new", data={}).status_code == 409
    assert DE["variant.new"] not in client.get(f"/templates/{template_id}").text


def test_unknown_template_is_404(client: AppClient) -> None:
    assert client.get("/templates/999/variants/new").status_code == 404
    assert client.post("/templates/999/variants/new", data={}).status_code == 404


# --- Test case 17e: inactive templates ------------------------------------------------------


def test_inactive_template_takes_no_new_variants_but_keeps_the_old(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)
    client.post(f"/templates/{setup.template_id}/deactivate", data={})

    assert client.get(f"/templates/{setup.template_id}/variants/new").status_code == 409
    response = make_variant(client, setup, colour="blau", number="3D-0042-BL")
    assert response.status_code == 409
    assert article_count() == 1
    assert article_by_number("3D-0042-RT").active is True
    assert DE["variant.new"] not in client.get(f"/templates/{setup.template_id}").text


def test_inactive_articles_leave_the_list_and_can_come_back(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id

    client.post(f"/articles/{article_id}/deactivate", data={})
    assert "3D-0042-RT" not in client.get("/articles").text
    assert "3D-0042-RT" in client.get("/articles?inactive=1").text
    client.post(f"/articles/{article_id}/activate", data={})
    assert "3D-0042-RT" in client.get("/articles").text


# --- Test cases 13-15: inheriting -----------------------------------------------------------


def row_of(html: str, label: str) -> str:
    """The text of the detail row with this label (between its dt and the next dt)."""
    start = html.index(f"<dt>{label}</dt>")
    end = html.find("<dt>", start + 1)
    return html[start : end if end != -1 else len(html)]


def test_variant_shows_inherited_values_and_marks_them(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id

    html = client.get(f"/articles/{article_id}").text

    for label, value in (
        ("article.field.selling_price", "10,00"),
        ("article.field.weight", "2,00"),
        ("article.field.min_stock", "5"),
    ):
        row = row_of(html, DE[label])
        assert value in row
        assert DE["variant.inherited"] in row
        assert DE["variant.own"] not in row


def test_variant_with_own_price_says_own_value(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup, selling_price="12,50")
    article_id = article_by_number("3D-0042-RT").id

    html = client.get(f"/articles/{article_id}").text

    row = row_of(html, DE["article.field.selling_price"])
    assert "12,50" in row
    assert DE["variant.own"] in row
    assert DE["variant.inherited"] not in row
    # The others are still inherited
    assert DE["variant.inherited"] in row_of(html, DE["article.field.weight"])
    assert DE["variant.inherited"] in row_of(html, DE["article.field.min_stock"])


def test_reset_to_template_value_removes_the_own_value(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup, selling_price="12,50", weight="3", min_stock="1")
    article_id = article_by_number("3D-0042-RT").id

    response = client.post(f"/articles/{article_id}/reset/selling_price", data={})

    assert response.status_code == 200
    article = article_by_number("3D-0042-RT")
    assert article.selling_price is None
    assert str(article.weight) == "3"  # others untouched
    row = row_of(client.get(f"/articles/{article_id}").text, DE["article.field.selling_price"])
    assert "10,00" in row
    assert DE["variant.inherited"] in row
    entry = log_entries("article.changed")[-1]
    assert json.loads(entry.details) == [{"field": "selling_price", "old": "12.50", "new": ""}]


def test_reset_button_only_where_there_is_an_own_value(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup, selling_price="12,50")
    article_id = article_by_number("3D-0042-RT").id

    html = client.get(f"/articles/{article_id}").text

    assert f'action="/articles/{article_id}/reset/selling_price"' in html
    assert f'action="/articles/{article_id}/reset/weight"' not in html
    assert DE["variant.reset"] in html


def test_reset_is_refused_for_normal_articles_and_unknown_fields(
    client: AppClient, setup: Setup
) -> None:
    client.post(
        "/articles/new",
        data={
            "number": "A-1",
            "name": "Normal",
            "selling_price": "4,00",
            "weight": "",
            "min_stock": "",
        },
    )
    normal_id = article_by_number("A-1").id
    make_variant(client, setup, selling_price="12,50")
    variant_id = article_by_number("3D-0042-RT").id

    assert client.post(f"/articles/{normal_id}/reset/selling_price", data={}).status_code == 404
    assert client.post(f"/articles/{variant_id}/reset/number", data={}).status_code == 404
    assert client.post("/articles/999/reset/weight", data={}).status_code == 404
    assert str(article_by_number("A-1").selling_price) == "4.00"
    assert str(article_by_number("3D-0042-RT").selling_price) == "12.50"


def test_template_change_reaches_only_variants_without_own_value(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)
    make_variant(client, setup, colour="blau", number="3D-0042-BL", selling_price="12,50")
    first = article_by_number("3D-0042-RT").id
    second = article_by_number("3D-0042-BL").id

    client.post(
        f"/templates/{setup.template_id}",
        data={
            "number": "V-1",
            "name": "Halter A",
            "description": "",
            "selling_price": "11,00",
            "weight": "2",
            "min_stock": "5",
            f"attr_{setup.colour_id}": "1",
            f"attr_{setup.size_id}": "1",
        },
    )

    assert "11,00" in row_of(
        client.get(f"/articles/{first}").text, DE["article.field.selling_price"]
    )
    assert "12,50" in row_of(
        client.get(f"/articles/{second}").text, DE["article.field.selling_price"]
    )
    assert "11,00" in client.get("/articles").text


def test_inherited_minimum_stock_counts_on_dashboard_and_stock_page(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id
    add_movement(article_id, 3)  # below the inherited minimum of 5

    dashboard = client.get("/").text
    assert "3D-0042-RT" in dashboard
    stock_page = client.get("/stock").text
    assert "3D-0042-RT" in stock_page

    add_movement(article_id, 2)  # equal to the minimum is fine
    assert "3D-0042-RT" not in client.get("/").text


def test_own_minimum_stock_wins_over_the_template(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup, min_stock="1")
    add_movement(article_by_number("3D-0042-RT").id, 3)

    assert "3D-0042-RT" not in client.get("/").text


# --- Editing a variant ----------------------------------------------------------------------


def edit_form(setup: Setup, colour="rot", size="L", **overrides) -> dict[str, str]:
    form = variant_form(setup, colour=colour, size=size)
    form.update(overrides)
    return form


def test_empty_field_in_the_edit_form_goes_back_to_the_template_value(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup, selling_price="12,50")
    article_id = article_by_number("3D-0042-RT").id

    response = client.post(
        f"/articles/{article_id}", data=edit_form(setup, selling_price=""), follow_redirects=False
    )

    assert response.status_code == 303
    assert article_by_number("3D-0042-RT").selling_price is None


def test_edit_form_of_a_variant_shows_values_and_hint(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id

    html = client.get(f"/articles/{article_id}/edit").text

    assert DE["variant.hint.inherit"] in html
    assert "Farbe" in html
    assert f'name="attr_{setup.colour_id}"' in html


def test_edit_form_of_a_normal_article_has_no_variant_parts(client: AppClient) -> None:
    client.post(
        "/articles/new",
        data={
            "number": "A-1",
            "name": "Normal",
            "selling_price": "",
            "weight": "",
            "min_stock": "",
        },
    )

    html = client.get(f"/articles/{article_by_number('A-1').id}/edit").text

    assert DE["variant.hint.inherit"] not in html
    assert "attr_" not in html


def test_changing_the_values_of_a_variant(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id

    response = client.post(
        f"/articles/{article_id}", data=edit_form(setup, colour="blau"), follow_redirects=False
    )

    assert response.status_code == 303
    assert value_texts(article_id) == {"Farbe": "blau", "Größe": "L"}
    details = json.loads(log_entries("article.changed")[-1].details)
    assert [d["field"] for d in details] == ["attribute_value"]


def test_edit_cannot_create_a_duplicate_combination(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    make_variant(client, setup, colour="blau", number="3D-0042-BL")
    blue_id = article_by_number("3D-0042-BL").id

    response = client.post(f"/articles/{blue_id}", data=edit_form(setup, number="3D-0042-BL"))

    assert response.status_code == 422
    assert DE["variant.error.combination_taken"] in response.text
    assert value_texts(blue_id)["Farbe"] == "blau"


def test_edit_refuses_an_invalid_value(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id
    form = edit_form(setup)
    form[f"attr_{setup.colour_id}"] = str(setup.values["L"])

    response = client.post(f"/articles/{article_id}", data=form)

    assert response.status_code == 422
    assert value_texts(article_id)["Farbe"] == "rot"


# --- Test case 16: adding an attribute to the template --------------------------------------


def add_third_attribute(client: AppClient, setup: Setup) -> int:
    make_attribute(client, "Material", "PLA\nPETG")
    material = attributes()[-1].id
    response = client.post(
        f"/templates/{setup.template_id}",
        data={
            "number": "V-1",
            "name": "Halter A",
            "description": "",
            "selling_price": "10,00",
            "weight": "2",
            "min_stock": "5",
            f"attr_{setup.colour_id}": "1",
            f"attr_{setup.size_id}": "1",
            f"attr_{material}": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return material


def test_new_attribute_keeps_variants_and_marks_the_value_as_missing(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id

    add_third_attribute(client, setup)

    assert article_count() == 1
    assert value_texts(article_id) == {"Farbe": "rot", "Größe": "L"}
    html = client.get(f"/articles/{article_id}").text
    assert "Material" in html
    assert DE["variant.missing"] in html
    assert DE["variant.missing"] in client.get(f"/templates/{setup.template_id}").text


def test_variant_with_a_missing_value_can_still_be_edited(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id
    material = add_third_attribute(client, setup)
    form = edit_form(setup, selling_price="9,00")
    form[f"attr_{material}"] = ""

    response = client.post(f"/articles/{article_id}", data=form, follow_redirects=False)

    assert response.status_code == 303
    assert str(article_by_number("3D-0042-RT").selling_price) == "9.00"
    assert "Material" not in value_texts(article_id)


def test_missing_value_can_be_filled_in_later(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id
    material = add_third_attribute(client, setup)
    with db() as session:
        petg = session.query(AttributeValue).filter_by(value="PETG").one().id
    form = edit_form(setup)
    form[f"attr_{material}"] = str(petg)

    response = client.post(f"/articles/{article_id}", data=form, follow_redirects=False)

    assert response.status_code == 303
    assert value_texts(article_id)["Material"] == "PETG"


def test_new_variants_need_all_attributes_including_the_new_one(
    client: AppClient, setup: Setup
) -> None:
    add_third_attribute(client, setup)

    response = make_variant(client, setup)

    assert response.status_code == 422
    assert DE["variant.error.value_required"] in response.text


# --- Test case 17: removing an attribute ----------------------------------------------------


def test_attribute_in_use_by_variants_cannot_be_removed_from_the_template(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)

    response = client.post(
        f"/templates/{setup.template_id}",
        data={
            "number": "V-1",
            "name": "Halter A",
            "description": "",
            "selling_price": "",
            "weight": "",
            "min_stock": "",
            f"attr_{setup.colour_id}": "1",  # size is removed
        },
    )

    assert response.status_code == 422
    assert DE["template.error.attribute_in_use"] in response.text
    assert template_attribute_names(setup.template_id) == ["Farbe", "Größe"]


def test_unused_attribute_can_still_be_removed(client: AppClient, setup: Setup) -> None:
    response = client.post(
        f"/templates/{setup.template_id}",
        data={
            "number": "V-1",
            "name": "Halter A",
            "description": "",
            "selling_price": "",
            "weight": "",
            "min_stock": "",
            f"attr_{setup.colour_id}": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


# --- Test case 18, 19: display ---------------------------------------------------------------


def test_variant_page_links_to_the_template_and_shows_the_values(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id

    html = client.get(f"/articles/{article_id}").text

    assert f'href="/templates/{setup.template_id}"' in html
    assert DE["variant.of_template"] in html
    assert "Halter A" in html
    for text in ("Farbe", "rot", "Größe", "L"):
        assert text in html


def test_template_page_lists_its_variants_with_links(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    make_variant(client, setup, colour="blau", number="3D-0042-BL", name="Halter A blau L")
    ids = [article_by_number(n).id for n in ("3D-0042-RT", "3D-0042-BL")]

    html = client.get(f"/templates/{setup.template_id}").text

    for article_id in ids:
        assert f'href="/articles/{article_id}"' in html
    assert "3D-0042-BL" in html
    assert f'href="/templates/{setup.template_id}/variants/new"' in html


def test_template_without_variants_says_so(client: AppClient, setup: Setup) -> None:
    assert DE["variant.none"] in client.get(f"/templates/{setup.template_id}").text


def test_normal_articles_are_unchanged(client: AppClient) -> None:
    client.post(
        "/articles/new",
        data={
            "number": "A-1",
            "name": "Normal",
            "selling_price": "4,00",
            "weight": "",
            "min_stock": "",
        },
    )
    article_id = article_by_number("A-1").id

    html = client.get(f"/articles/{article_id}").text

    assert f"<dt>{DE['variant.of_template']}</dt>" not in html
    assert DE["variant.inherited"] not in html
    assert DE["variant.own"] not in html
    assert DE["variant.reset"] not in html
    assert "4,00" in html


# --- Test cases 17a-17c: deleting articles --------------------------------------------------


def delete(client: AppClient, article_id: int):
    return client.post(f"/articles/{article_id}/delete", data={}, follow_redirects=False)


def plain_article(number: str = "A-1") -> int:
    return add_article(number, "Kabelhalter")


def test_nothing_linked_offers_delete_with_confirmation(client: AppClient) -> None:
    article_id = plain_article()

    assert DE["article.delete"] in client.get(f"/articles/{article_id}").text
    page = client.get(f"/articles/{article_id}/delete")
    assert page.status_code == 200
    assert DE["article.delete.confirm"] in page.text
    # Looking at the question deletes nothing
    assert article_count() == 1

    response = delete(client, article_id)

    assert response.status_code == 303
    assert response.headers["location"] == "/articles"
    assert article_count() == 0
    assert client.get(f"/articles/{article_id}").status_code == 404


def test_without_confirmation_the_article_stays(client: AppClient) -> None:
    article_id = plain_article()

    client.get(f"/articles/{article_id}/delete")
    client.get(f"/articles/{article_id}")

    assert article_count() == 1


def test_only_calculations_linked_names_them_and_advises_deactivating(
    client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Calculations do not exist yet (M3); the counts say what will hang on an article then.
    real = article_purge.counts

    def with_calculations(db, article_id):
        return {**real(db, article_id), "calculations": 3}

    monkeypatch.setattr(article_purge, "counts", with_calculations)
    article_id = plain_article()

    page = client.get(f"/articles/{article_id}/delete")

    assert page.status_code == 200
    assert f"{DE['article.purge.count.calculations']}: 3" in page.text
    assert DE["article.delete.advice"] in page.text
    assert delete(client, article_id).status_code == 303
    assert article_count() == 0


def test_stock_movements_block_deleting(client: AppClient) -> None:
    article_id = plain_article()
    add_movement(article_id, 5)

    html = client.get(f"/articles/{article_id}").text
    assert f'href="/articles/{article_id}/delete"' not in html
    assert DE["article.delete.blocked"] in html
    assert client.get(f"/articles/{article_id}/delete").status_code == 409
    assert delete(client, article_id).status_code == 409
    assert article_count() == 1


def test_productions_and_sales_block_deleting_too(
    client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = article_purge.counts
    article_id = plain_article()
    for key in ("productions", "sales"):

        def counted(db, article_id, key=key):
            return {**real(db, article_id), key: 1}

        monkeypatch.setattr(article_purge, "counts", counted)
        assert delete(client, article_id).status_code == 409
    assert article_count() == 1


def test_delete_of_a_variant_takes_its_values_along(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id

    assert delete(client, article_id).status_code == 303

    assert article_count() == 0
    with db() as session:
        assert session.query(ArticleAttributeValue).count() == 0
        assert session.query(ArticleTemplate).count() == 1


def test_unknown_article_delete_is_404(client: AppClient) -> None:
    assert client.get("/articles/999/delete").status_code == 404
    assert delete(client, 999).status_code == 404


# --- Test case 17d: deleting templates ------------------------------------------------------


def test_template_with_variants_cannot_be_deleted(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)

    html = client.get(f"/templates/{setup.template_id}").text
    assert f'href="/templates/{setup.template_id}/delete"' not in html
    assert DE["template.delete.blocked"] in html
    assert client.get(f"/templates/{setup.template_id}/delete").status_code == 409
    response = client.post(f"/templates/{setup.template_id}/delete", data={})
    assert response.status_code == 409
    assert len(templates()) == 1


def test_template_can_be_deleted_once_the_variants_are_gone(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)
    delete(client, article_by_number("3D-0042-RT").id)

    response = client.post(
        f"/templates/{setup.template_id}/delete", data={}, follow_redirects=False
    )

    assert response.status_code == 303
    assert templates() == []


def test_deactivated_variants_still_block_deleting_the_template(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)
    client.post(f"/articles/{article_by_number('3D-0042-RT').id}/deactivate", data={})

    assert client.post(f"/templates/{setup.template_id}/delete", data={}).status_code == 409


# --- Test case 17f and 22: change log -------------------------------------------------------


def test_variant_and_delete_are_in_the_change_log(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id
    client.post(f"/articles/{article_id}/deactivate", data={})
    plain = plain_article()
    delete(client, plain)

    created = log_entries("article.variant_created")
    assert [e.subject for e in created] == ["3D-0042-RT"]
    details = json.loads(created[0].details)
    assert {"field": "template", "old": "", "new": "Halter A"} in details
    assert {"field": "attribute_value", "old": "", "new": "Farbe: rot"} in details
    assert [e.subject for e in log_entries("article.deactivated")] == ["3D-0042-RT"]
    assert [e.subject for e in log_entries("article.deleted")] == ["A-1 · Kabelhalter"]


def test_log_page_shows_the_new_entries(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)
    article_id = article_by_number("3D-0042-RT").id
    client.post(f"/articles/{article_id}", data=edit_form(setup, colour="blau"))
    delete(client, article_id)

    html = client.get("/settings/log").text

    assert DE["log.action.article.variant_created"] in html
    assert DE["log.action.article.deleted"] in html
    assert f"{DE['article.field.attribute_value']}: Farbe: rot → Farbe: blau" in html
    assert f"{DE['article.field.template']}: - → Halter A" in html


# --- Test cases 17f and 20: rights ----------------------------------------------------------

EDIT_ROUTES = [
    ("get", "/templates/1/variants/new"),
    ("post", "/templates/1/variants/new"),
    ("post", "/articles/1/reset/selling_price"),
    ("get", "/articles/1/delete"),
    ("post", "/articles/1/delete"),
]


@pytest.mark.parametrize(("method", "path"), EDIT_ROUTES)
def test_read_only_user_cannot_change_anything(
    client: AppClient, reader: AppClient, setup: Setup, method: str, path: str
) -> None:
    make_variant(client, setup)

    response = reader.post(path, data={}) if method == "post" else reader.get(path)

    assert response.status_code == 403
    assert article_count() == 1


def test_read_only_user_sees_no_buttons(client: AppClient, reader: AppClient, setup: Setup) -> None:
    make_variant(client, setup, selling_price="12,50")
    article_id = article_by_number("3D-0042-RT").id

    html = reader.get(f"/articles/{article_id}").text
    assert "/reset/" not in html
    assert "/delete" not in html
    assert "/variants/new" not in reader.get(f"/templates/{setup.template_id}").text


def test_employee_can_create_and_delete(
    client: AppClient, employee: AppClient, setup: Setup
) -> None:
    assert make_variant(employee, setup).status_code == 303
    article_id = article_by_number("3D-0042-RT").id

    assert delete(employee, article_id).status_code == 303


def test_without_login_the_pages_redirect_to_login(client: AppClient, setup: Setup) -> None:
    with AppClient(create_app()) as anonymous:
        for path in (f"/templates/{setup.template_id}/variants/new", "/articles/1/delete"):
            response = anonymous.get(path, follow_redirects=False)
            assert response.status_code in (302, 303, 307)
            assert "/login" in response.headers["location"]


# --- Test case 21: form token (CSRF) --------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), [r for r in EDIT_ROUTES if r[0] == "post"])
def test_post_without_form_token_is_refused(
    client: AppClient, setup: Setup, method: str, path: str
) -> None:
    make_variant(client, setup, selling_price="12,50")
    client.csrf_token = None

    response = client.post(path, data=variant_form(setup))

    assert response.status_code == 403
    assert article_count() == 1
    assert article_by_number("3D-0042-RT").selling_price is not None


def test_forms_carry_the_token(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup, selling_price="12,50")
    article_id = article_by_number("3D-0042-RT").id
    plain = plain_article()

    for path in (
        f"/templates/{setup.template_id}/variants/new",
        f"/articles/{article_id}",
        f"/articles/{plain}/delete",
    ):
        assert 'name="csrf_token"' in client.get(path).text, path


# --- Test case 23: migration ----------------------------------------------------------------


def test_migration_keeps_existing_articles_as_normal_articles(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    engine = make_engine(path)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0009")
    with sqlite3.connect(path) as raw:
        raw.execute(
            "INSERT INTO articles (number, number_key, name, active, created_at)"
            " VALUES ('3D-1', '3d-1', 'Altbestand', 1, '2026-01-01 00:00:00')"
        )

    run_migrations(engine)

    with Session(engine) as session:
        article = session.query(Article).one()
        assert (article.number, article.template_id) == ("3D-1", None)
        assert session.query(ArticleAttributeValue).count() == 0


def test_migration_can_be_undone(tmp_path: Path) -> None:
    path = tmp_path / "down.db"
    engine = make_engine(path)
    run_migrations(engine)

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0009")
    with sqlite3.connect(path) as raw:
        tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master")}
        columns = {row[1] for row in raw.execute("PRAGMA table_info(articles)")}
    assert "article_attribute_values" not in tables
    assert "template_id" not in columns
    run_migrations(engine)


# --- Test case 24: texts from the translation files -----------------------------------------


def test_all_new_texts_exist_in_both_languages() -> None:
    german = {key for key in DE if key.startswith(PREFIXES)}
    english = {key for key in EN if key.startswith(PREFIXES)}

    assert german
    assert german == english
    assert "article.field.template" in DE
    assert "article.field.template" in EN
    assert "article.field.attribute_value" in EN
    assert "template.delete.blocked" in EN


def test_english_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_LANGUAGE", "en")
    with signed_in_client() as client:
        setup = make_setup(client)
        make_variant(client, setup)
        html = client.get(f"/articles/{article_by_number('3D-0042-RT').id}").text

    assert EN["variant.inherited"] in html
    assert EN["variant.of_template"] in html


def test_pages_escape_text(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup, name="<b>fett</b>")

    html = client.get(f"/templates/{setup.template_id}").text

    assert "<b>fett</b>" not in html


# --- Issue #68: inherited values as grey placeholders -----------------------------------------


def placeholder_of(html: str, field: str) -> str | None:
    match = re.search(rf'<input[^>]*id="{field}"[^>]*>', html)
    assert match, field
    found = re.search(r'placeholder="([^"]*)"', match.group(0))
    return found.group(1) if found else None


def value_of(html: str, field: str) -> str:
    match = re.search(rf'<input[^>]*id="{field}"[^>]*>', html)
    return re.search(r'value="([^"]*)"', match.group(0)).group(1)


def test_new_variant_form_shows_template_values_as_placeholders(
    client: AppClient, setup: Setup
) -> None:
    html = client.get(f"/templates/{setup.template_id}/variants/new").text

    assert placeholder_of(html, "selling_price") == "10,00 (von Vorlage)"
    assert placeholder_of(html, "weight") == "2,00 (von Vorlage)"
    assert placeholder_of(html, "min_stock") == "5 (von Vorlage)"
    # Only a hint: the fields stay empty and so keep inheriting.
    assert [value_of(html, f) for f in ("selling_price", "weight", "min_stock")] == ["", "", ""]


def test_saving_without_input_still_inherits_and_own_input_wins(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup)
    make_variant(client, setup, colour="blau", number="3D-0042-BL", selling_price="15,00")

    assert article_by_number("3D-0042-RT").selling_price is None
    assert str(article_by_number("3D-0042-BL").selling_price) == "15.00"


def test_no_placeholder_for_a_field_the_template_has_no_value_for(client: AppClient) -> None:
    setup = make_setup(client, selling_price="", weight="", min_stock="")

    html = client.get(f"/templates/{setup.template_id}/variants/new").text

    assert [placeholder_of(html, f) for f in ("selling_price", "weight", "min_stock")] == [None] * 3


def test_edit_form_of_a_variant_shows_placeholders_only_for_empty_fields(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup, selling_price="15,00")
    article_id = article_by_number("3D-0042-RT").id

    html = client.get(f"/articles/{article_id}/edit").text

    assert value_of(html, "selling_price") == "15,00"
    assert placeholder_of(html, "weight") == "2,00 (von Vorlage)"
    assert placeholder_of(html, "min_stock") == "5 (von Vorlage)"


def test_normal_article_forms_have_no_placeholders(client: AppClient) -> None:
    article_id = plain_article()

    for path in ("/articles/new", f"/articles/{article_id}/edit"):
        html = client.get(path).text
        assert placeholder_of(html, "selling_price") is None, path


def test_placeholder_text_exists_in_both_languages() -> None:
    assert "{value}" in DE["variant.placeholder"] and "{value}" in EN["variant.placeholder"]
