"""Creating all variant combinations at once (issue #50). All data is invented.

Test case numbers refer to the confirmed list in issues #44 and #50.
"""

import re

import pytest
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import Article, ChangeLogEntry
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_templates import field_error, make_template, templates
from tests.test_variants import (
    DE,
    EN,
    Setup,
    article_by_number,
    article_count,
    db,
    make_setup,
    make_variant,
    value_texts,
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


@pytest.fixture
def setup(client: AppClient) -> Setup:
    return make_setup(client)


def url(setup: Setup) -> str:
    return f"/templates/{setup.template_id}/variants/all"


def numbers_in(html: str) -> dict[int, str]:
    """index -> prefilled number of the rows of the preview."""
    found = re.findall(r'name="number_(\d+)"[^>]*value="([^"]*)"', html)
    return {int(index): value for index, value in found}


def form_from(html: str, **overrides: str) -> dict[str, str]:
    form = {f"number_{index}": value for index, value in numbers_in(html).items()}
    form.update(overrides)
    return form


def template_number(setup: Setup) -> str:
    return next(t.number for t in templates() if t.id == setup.template_id)


# --- Test case 10: preview, then create ------------------------------------------------------


def test_preview_shows_all_four_combinations_with_suggested_numbers_and_saves_nothing(
    client: AppClient, setup: Setup
) -> None:
    response = client.get(url(setup))

    assert response.status_code == 200
    prefix = template_number(setup)
    assert sorted(numbers_in(response.text).values()) == sorted(
        f"{prefix}-{c}-{s}" for c in ("ro", "bl") for s in ("m", "l")
    )
    assert article_count() == 0
    assert 'name="csrf_token"' in response.text


def test_confirming_creates_four_articles_with_the_values(client: AppClient, setup: Setup) -> None:
    html = client.get(url(setup)).text

    response = client.post(url(setup), data=form_from(html), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/templates/{setup.template_id}?saved=1"
    assert article_count() == 4
    prefix = template_number(setup)
    article = article_by_number(f"{prefix}-ro-l")
    assert article.template_id == setup.template_id
    assert article.active is True
    # Price, weight and minimum stock are inherited, not copied.
    assert article.selling_price is None
    assert value_texts(article.id) == {"Farbe": "rot", "Größe": "L"}
    with db() as session:
        assert (
            session.query(ChangeLogEntry).filter_by(action="article.variant_created").count() == 4
        )


def test_numbers_can_be_changed_in_the_preview(client: AppClient, setup: Setup) -> None:
    html = client.get(url(setup)).text

    client.post(url(setup), data=form_from(html, number_0="MY-OWN-1"), follow_redirects=False)

    assert article_by_number("MY-OWN-1").template_id == setup.template_id
    assert article_count() == 4


# --- Test case 11: existing combinations are skipped ----------------------------------------


def test_existing_combination_is_marked_and_skipped(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)  # rot + L

    html = client.get(url(setup)).text

    assert len(numbers_in(html)) == 3
    assert html.count(f"<em>{DE['variant.bulk.exists']}</em>") == 1
    client.post(url(setup), data=form_from(html), follow_redirects=False)
    assert article_count() == 4
    assert article_by_number("3D-0042-RT").name == "Halter A rot L"  # untouched


def test_when_everything_exists_there_is_nothing_to_create(client: AppClient, setup: Setup) -> None:
    html = client.get(url(setup)).text
    client.post(url(setup), data=form_from(html), follow_redirects=False)

    html = client.get(url(setup)).text

    assert numbers_in(html) == {}
    assert DE["variant.bulk.nothing_new"] in html
    assert article_count() == 4


# --- Test case 12: taken or duplicate numbers -----------------------------------------------


def test_taken_number_saves_nothing_and_shows_the_error_row(
    client: AppClient, setup: Setup
) -> None:
    make_variant(client, setup, number="TAKEN-1", colour="rot", size="M")
    html = client.get(url(setup)).text

    response = client.post(url(setup), data=form_from(html, number_1="taken-1"))

    assert response.status_code == 422
    assert field_error(response.text, "number_1") == DE["article.error.taken"]
    assert article_count() == 1


def test_duplicate_number_within_the_preview_saves_nothing(client: AppClient, setup: Setup) -> None:
    html = client.get(url(setup)).text

    response = client.post(url(setup), data=form_from(html, number_0="SAME", number_1="Same"))

    assert response.status_code == 422
    assert field_error(response.text, "number_1") == DE["article.error.taken"]
    assert article_count() == 0


def test_template_number_and_empty_number_are_refused(client: AppClient, setup: Setup) -> None:
    html = client.get(url(setup)).text

    response = client.post(
        url(setup), data=form_from(html, number_0=template_number(setup), number_1="")
    )

    assert response.status_code == 422
    assert field_error(response.text, "number_0") == DE["article.error.template_number"]
    assert field_error(response.text, "number_1") == DE["article.error.required"]
    assert article_count() == 0
    # What the person typed stays in the form.
    assert f'value="{template_number(setup)}"' in response.text


def test_row_for_an_existing_combination_cannot_be_forced(client: AppClient, setup: Setup) -> None:
    make_variant(client, setup)  # rot + L is combination 1 of the full list
    html = client.get(url(setup)).text

    client.post(url(setup), data=form_from(html, number_1="FORCED", number_99="X"))

    assert article_count() == 4
    with db() as session:
        assert session.query(Article).filter_by(number="FORCED").count() == 0


# --- Template state -------------------------------------------------------------------------


def test_button_on_the_template_page_only_when_possible(client: AppClient, setup: Setup) -> None:
    assert url(setup) in client.get(f"/templates/{setup.template_id}").text
    client.post(f"/templates/{setup.template_id}/deactivate", data={})
    assert url(setup) not in client.get(f"/templates/{setup.template_id}").text
    assert client.get(url(setup)).status_code == 409


def test_template_without_attributes_and_unknown_template(client: AppClient) -> None:
    make_template(client)
    template_id = templates()[0].id

    assert client.get(f"/templates/{template_id}/variants/all").status_code == 409
    assert client.get("/templates/9999/variants/all").status_code == 404


# --- Rights, CSRF, text ---------------------------------------------------------------------


def test_reader_is_refused_and_employee_may_create(
    client: AppClient, reader: AppClient, employee: AppClient, setup: Setup
) -> None:
    assert reader.get(url(setup)).status_code == 403
    assert reader.post(url(setup), data={}).status_code == 403
    assert url(setup) not in reader.get(f"/templates/{setup.template_id}").text
    html = employee.get(url(setup)).text
    assert (
        employee.post(url(setup), data=form_from(html), follow_redirects=False).status_code == 303
    )
    assert article_count() == 4


def test_post_without_form_token_is_refused(client: AppClient, setup: Setup) -> None:
    html = client.get(url(setup)).text
    client.csrf_token = None

    assert client.post(url(setup), data=form_from(html)).status_code == 403
    assert article_count() == 0


def test_without_login_the_page_redirects_to_login(client: AppClient, setup: Setup) -> None:
    with AppClient(create_app()) as anonymous:
        response = anonymous.get(url(setup), follow_redirects=False)
        assert "/login" in response.headers["location"]


def test_texts_exist_in_german_and_english() -> None:
    keys = [k for k in DE if k.startswith("variant.bulk")]
    assert len(keys) >= 6
    assert all(EN.get(k) for k in keys)


def test_values_are_escaped(client: AppClient, setup: Setup) -> None:
    html = client.get(url(setup)).text
    response = client.post(url(setup), data=form_from(html, number_0='"><b>x</b>'))
    assert "<b>x</b>" not in response.text
    assert unused(response) is None


def unused(response) -> None:  # pragma: no cover - keeps the helper section tidy
    return None


def _session() -> Session:  # pragma: no cover
    return db()


def test_too_many_combinations_are_refused(client: AppClient, setup: Setup, monkeypatch) -> None:
    monkeypatch.setattr("app.variant_bulk.MAX_COMBINATIONS", 3)

    assert client.get(url(setup)).status_code == 409
