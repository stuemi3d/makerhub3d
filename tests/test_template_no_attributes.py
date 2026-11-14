"""Template form without any attribute: a hint instead of a link (issue #102, test cases 1-5).

All data is invented. The test case numbers refer to the comment on the issue.
"""

import pytest

from tests.auth import AppClient, signed_in_client
from tests.test_templates import DE, EN, make_attribute, make_template, template_form, templates

HX = {"HX-Request": "true"}
HINT = "template.no_attributes"


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def form_paths(client: AppClient) -> list[str]:
    make_template(client, number="V-1", name="Halter")
    return ["/templates/new", f"/templates/{templates()[0].id}/edit"]


# --- Case 1: no attribute, no link, only the hint --------------------------------------------


@pytest.mark.parametrize("headers", [{}, HX], ids=["page", "pop-up"])
def test_without_attributes_the_form_shows_a_hint_and_no_link(
    client: AppClient, headers: dict[str, str]
) -> None:
    for path in form_paths(client):
        html = client.get(path, headers=headers).text

        assert DE[HINT] in html, path
        assert 'href="/attributes/new"' not in html, path
        # Inside the form there is no link at all to the attribute pages.
        form = html.split("<form", 1)[1].split("</form>", 1)[0]
        assert "/attributes" not in form, path


# --- Case 2: with attributes the hint is gone and the choice is there -------------------------


@pytest.mark.parametrize("headers", [{}, HX], ids=["page", "pop-up"])
def test_with_attributes_there_is_no_hint(client: AppClient, headers: dict[str, str]) -> None:
    make_attribute(client)
    for path in form_paths(client):
        html = client.get(path, headers=headers).text

        assert DE[HINT] not in html, path
        assert 'name="attr_1"' in html, path


# --- Case 3: a template without attributes can still be saved ---------------------------------


def test_a_template_without_attributes_can_be_saved(client: AppClient) -> None:
    response = client.post(
        "/templates/new", data=template_form(client, number="V-9", name="Ohne"), headers=HX
    )

    assert response.status_code == 204
    assert [item.name for item in templates()] == ["Ohne"]


# --- Case 4: in the pop-up the entries stay when saving fails ---------------------------------


def test_the_pop_up_keeps_the_entries_and_the_hint_after_an_error(client: AppClient) -> None:
    response = client.post(
        "/templates/new", data=template_form(client, number="V-5", name=""), headers=HX
    )

    assert response.status_code == 422
    assert "<html" not in response.text
    assert 'value="V-5"' in response.text
    assert DE[HINT] in response.text and 'href="/attributes/new"' not in response.text


# --- Case 5: the text comes from the translation files ----------------------------------------


def test_the_hint_is_in_both_languages_and_names_the_place_to_create_attributes() -> None:
    assert DE[HINT].startswith("Es gibt noch keine Merkmale.") and "Artikel → Merkmale" in DE[HINT]
    assert EN[HINT].startswith("There are no attributes yet.")
    assert "Articles → Attributes" in EN[HINT]
