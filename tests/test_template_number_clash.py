"""Article and variant numbers must not equal a template number (issue #65). Invented data."""

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.auth import AppClient
from tests.test_stock import add_article
from tests.test_templates import db, field_error, make_template
from tests.test_variants import Setup, make_setup, make_variant

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))


def article_form(number: str, name: str = "Kabelhalter") -> dict[str, str]:
    return {"number": number, "name": name, "selling_price": "", "weight": "", "min_stock": ""}


@pytest.mark.parametrize("number", ["123456", "123456 ", "V-ABC", "v-abc"])
def test_new_article_with_a_template_number_is_refused(client: AppClient, number: str) -> None:
    make_template(client, number="123456")
    make_template(client, number="V-abc", name="Zweite")

    response = client.post("/articles/new", data=article_form(number))

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["article.error.template_number"]


def test_a_free_number_still_works(client: AppClient) -> None:
    make_template(client, number="123456")

    response = client.post("/articles/new", data=article_form("123457"), follow_redirects=False)

    assert response.status_code == 303


def test_new_variant_with_a_template_number_is_refused(client: AppClient) -> None:
    setup = make_setup(client)
    with db() as session:
        number = session.execute(text("SELECT number FROM article_templates")).scalar()

    response = make_variant(client, setup, number=str(number).upper())

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["article.error.template_number"]


def test_changing_an_article_number_to_a_template_number_is_refused(client: AppClient) -> None:
    make_template(client, number="123456")
    article_id = add_article("3D-0001")

    response = client.post(f"/articles/{article_id}", data=article_form("123456"))

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["article.error.template_number"]


def test_changing_a_variant_number_to_a_template_number_is_refused(client: AppClient) -> None:
    setup: Setup = make_setup(client)
    make_variant(client, setup)
    with db() as session:
        number = session.execute(text("SELECT number FROM article_templates")).scalar()
        article_id = session.execute(text("SELECT id FROM articles")).scalar()
    form = article_form(str(number), "Halter A rot L")
    form.update({f"attr_{setup.colour_id}": str(setup.values["rot"])})
    form.update({f"attr_{setup.size_id}": str(setup.values["L"])})

    response = client.post(f"/articles/{article_id}", data=form)

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["article.error.template_number"]


def test_an_unchanged_own_number_stays_allowed(client: AppClient) -> None:
    # An article that got its number before the template did (or was changed by hand in the
    # database) can still be saved with that same number.
    article_id = add_article("123456")
    make_template(client, number="999")
    with db() as session:
        session.execute(text("UPDATE article_templates SET number='123456', number_key='123456'"))
        session.commit()

    response = client.post(
        f"/articles/{article_id}", data=article_form("123456", "Neu"), follow_redirects=False
    )

    assert response.status_code == 303


def test_new_texts_exist_in_both_languages() -> None:
    assert DE["article.error.template_number"]
    assert EN["article.error.template_number"]
