"""Templates and attributes in the new style (issue #78, part 6a, test cases 1-8).

All data is invented. The test case numbers refer to the comment on the issue. The phone
view is checked in tests/browser/test_templates.py.
"""

import re

import pytest

from tests.auth import READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.test_templates import DE, attributes, make_attribute, make_template

HX = {"HX-Request": "true"}


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


def names_in(html: str) -> list[str]:
    return re.findall(r'<td class="cell-strong">(.*?)</td>', html)


def seed_templates(client: AppClient) -> None:
    make_template(client, number="V-1", name="Zebra", selling_price="5,00")
    make_template(client, number="V-2", name="Anker", selling_price="12,00")
    make_template(client, number="V-3", name="Mitte")


# --- Case 1: the list is a table with sortable columns and chips ------------------------------


def test_template_list_is_a_sortable_table_with_chips(client: AppClient) -> None:
    seed_templates(client)
    html = client.get("/templates").text

    assert '<table class="data-table">' in html
    assert 'class="chip chip-active"' in html
    assert names_in(html) == ["V-2", "V-3", "V-1"]  # default: by name
    assert names_in(client.get("/templates?sort=number&dir=desc").text) == ["V-3", "V-2", "V-1"]
    assert names_in(client.get("/templates?sort=name&dir=asc").text) == ["V-2", "V-3", "V-1"]
    # Without a price last, whichever the direction.
    assert names_in(client.get("/templates?sort=price&dir=asc").text) == ["V-1", "V-2", "V-3"]
    assert names_in(client.get("/templates?sort=price&dir=desc").text) == ["V-2", "V-1", "V-3"]
    assert 'aria-sort="ascending"' in client.get("/templates?sort=name").text


def test_template_list_ignores_a_bad_sort_and_keeps_inactive_toggle(client: AppClient) -> None:
    seed_templates(client)

    html = client.get("/templates?sort=%27;drop&dir=sideways").text

    assert names_in(html) == ["V-2", "V-3", "V-1"]
    assert "/templates?inactive=1" in html


def test_inactive_templates_show_an_inactive_chip(client: AppClient) -> None:
    make_template(client, number="V-1", name="Alt")
    client.post("/templates/1/deactivate", data={})

    html = client.get("/templates?inactive=1").text

    assert 'class="chip chip-inactive"' in html
    assert names_in(client.get("/templates").text) == []


# --- Case 2: the detail page is a card ---------------------------------------------------------


def test_template_detail_is_a_card_with_chip_and_variant_table(client: AppClient) -> None:
    make_template(client, number="V-1", name="Halter")
    html = client.get("/templates/1").text

    assert '<article class="detail-card">' in html
    assert '<dl class="details">' in html
    assert 'class="chip chip-active"' in html
    assert "Halter" in html


# --- Case 3: a reader sees everything but no buttons -------------------------------------------


def test_a_reader_sees_templates_and_attributes_without_buttons(
    client: AppClient, reader: AppClient
) -> None:
    make_attribute(client)
    make_template(client, number="V-1", name="Halter")

    for path in ("/templates", "/templates/1", "/attributes"):
        response = reader.get(path)
        assert response.status_code == 200, path
        assert "hx-get=" not in response.text.split("<main", 1)[1], path
        assert "/attributes/new" not in response.text.split("<main", 1)[1], path
        assert "/templates/new" not in response.text.split("<main", 1)[1], path
    assert reader.get("/attributes/new", headers=HX).status_code == 403


# --- Cases 4-6: the attribute is created in a pop-up ------------------------------------------


def test_new_attribute_button_opens_the_pop_up_and_the_list_refreshes(client: AppClient) -> None:
    html = client.get("/attributes").text

    link = re.search(r'<a [^>]*href="/attributes/new"[^>]*>', html)
    assert link and 'hx-get="/attributes/new"' in link.group(0) and "data-popup" in link.group(0)
    assert re.search(r"<[^>]*data-popup-refresh", html)
    assert '<dialog id="popup"' in html


def test_attribute_pop_up_is_only_the_form_and_the_page_still_works(client: AppClient) -> None:
    popup = client.get("/attributes/new", headers=HX)
    page = client.get("/attributes/new")

    assert popup.status_code == page.status_code == 200
    assert "<html" not in popup.text and "<form" in popup.text
    assert "hx-post" in popup.text and "hx-post" not in page.text
    assert 'id="popup-title"' in popup.text
    assert "<html" in page.text and "topbar" in page.text
    assert "HX-Request" in popup.headers["vary"]


def test_saving_an_attribute_in_the_pop_up_closes_it(client: AppClient) -> None:
    response = client.post(
        "/attributes/new", data={"name": "Farbe", "values": "rot = ro"}, headers=HX
    )

    assert response.status_code == 204
    assert "popup-saved" in response.headers["hx-trigger"]
    assert [a.name for a in attributes()] == ["Farbe"]
    assert "Farbe" in client.get("/attributes").text


def test_an_invalid_attribute_keeps_the_pop_up_open_with_the_error(client: AppClient) -> None:
    response = client.post("/attributes/new", data={"name": "", "values": ""}, headers=HX)

    assert response.status_code == 422
    assert "<html" not in response.text
    assert 'id="popup-title"' in response.text and "hx-post" in response.text
    assert 'aria-invalid="true"' in response.text
    assert attributes() == []


def test_without_the_pop_up_saving_still_redirects(client: AppClient) -> None:
    response = client.post(
        "/attributes/new", data={"name": "Farbe", "values": "rot = ro"}, follow_redirects=False
    )

    assert response.status_code == 303 and response.headers["location"] == "/attributes"


# --- Case 7: attributes are cards, adding a value works as before ------------------------------


def test_attributes_are_cards_and_a_value_can_be_added(client: AppClient) -> None:
    make_attribute(client)
    html = client.get("/attributes").text
    assert 'class="detail-card"' in html and "rot" in html

    client.post(
        f"/attributes/{attributes()[0].id}/values", data={"value": "grün", "abbreviation": "gr"}
    )
    assert "grün (gr)" in client.get("/attributes").text

    duplicate = client.post(
        f"/attributes/{attributes()[0].id}/values", data={"value": "grün", "abbreviation": "g2"}
    )
    assert duplicate.status_code == 422
    assert DE["attribute.error.value_taken"] in duplicate.text


# --- Case 8: login is still required -----------------------------------------------------------


def test_everything_needs_a_login() -> None:
    from app.main import create_app

    with AppClient(create_app()) as anonymous:
        for path in ("/templates", "/templates/1", "/attributes", "/attributes/new"):
            response = anonymous.get(path, follow_redirects=False)
            assert response.status_code in (303, 401), path
