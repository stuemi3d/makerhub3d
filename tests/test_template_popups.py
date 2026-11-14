"""Template create and edit in pop-ups (issue #100, test cases 1-16).

All data is invented. The test case numbers refer to the comment on the issue. Opening,
closing, focus and the phone view are checked in tests/browser/test_template_popups.py;
here the server side is checked.
"""

import re

import pytest

from app.main import create_app
from tests.auth import READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.test_templates import (
    attributes,
    field_error,
    log_actions,
    make_attribute,
    make_template,
    template_attribute_names,
    template_form,
    templates,
)

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


def first_template_id(client: AppClient) -> int:
    make_template(client, number="V-1", name="Halter")
    return templates()[0].id


# --- Cases 1, 6: the buttons open the pop-up and keep a normal address -------------------------


def test_list_buttons_open_the_pop_ups(client: AppClient) -> None:
    template_id = first_template_id(client)
    html = client.get("/templates").text

    for path in ("/templates/new", f"/templates/{template_id}/edit"):
        link = re.search(rf'<a [^>]*href="{re.escape(path)}"[^>]*>', html)
        assert link, path
        assert f'hx-get="{path}"' in link.group(0) and "data-popup" in link.group(0)
    assert '<dialog id="popup"' in html
    assert re.search(r"<[^>]*data-popup-refresh", html)


def test_detail_edit_button_opens_the_pop_up_and_the_page_refreshes(client: AppClient) -> None:
    template_id = first_template_id(client)
    html = client.get(f"/templates/{template_id}").text

    path = f"/templates/{template_id}/edit"
    link = re.search(rf'<a [^>]*href="{re.escape(path)}"[^>]*>', html)
    assert link and f'hx-get="{path}"' in link.group(0) and "data-popup" in link.group(0)
    # The whole detail (title and card) is refreshed, not only the variants.
    assert re.search(r'<div id="template-detail"[^>]*data-popup-refresh', html)


def test_empty_list_button_also_opens_the_pop_up(client: AppClient) -> None:
    html = client.get("/templates").text
    assert 'hx-get="/templates/new"' in html


# --- The pop-up is the same form as the page ---------------------------------------------------


@pytest.mark.parametrize("edit", [False, True])
def test_pop_up_is_only_the_form_and_the_page_still_works(client: AppClient, edit: bool) -> None:
    make_attribute(client)
    path = f"/templates/{first_template_id(client)}/edit" if edit else "/templates/new"
    popup = client.get(path, headers=HX)
    page = client.get(path)

    assert popup.status_code == page.status_code == 200
    assert "<html" not in popup.text and "topbar" not in popup.text and "<form" in popup.text
    assert "<html" in page.text and "topbar" in page.text
    assert "HX-Request" in popup.headers["vary"]
    for field in ("number", "name", "description", "selling_price", "weight", "min_stock"):
        assert f'name="{field}"' in popup.text and f'name="{field}"' in page.text
    assert 'name="attr_1"' in popup.text
    assert "hx-post" in popup.text and "hx-post" not in page.text
    assert re.search(r'name="csrf_token" value="[^"]+"', popup.text)
    assert 'id="popup-title"' in popup.text and 'id="popup-title"' not in page.text
    assert "autofocus" in popup.text
    # Cancel closes the pop-up; on the page it is a link back.
    assert "data-popup-close" in popup.text and "data-popup-close" not in page.text
    assert 'class="form-actions"' in popup.text and 'class="form-actions"' in page.text


# --- Cases 2, 5: create in the pop-up ---------------------------------------------------------


def test_saving_a_new_template_in_the_pop_up_closes_it(client: AppClient) -> None:
    make_attribute(client)
    form = template_form(client, number="V-7", name="Anker", selling_price="9,50")
    response = client.post("/templates/new", data={**form, "attr_1": "1"}, headers=HX)

    assert response.status_code == 204
    assert "popup-saved" in response.headers["hx-trigger"]
    [stored] = templates()
    assert (stored.number, stored.name) == ("V-7", "Anker")
    assert template_attribute_names(stored.id) == [attributes()[0].name]
    assert "Anker" in client.get("/templates").text
    assert log_actions()[-1] == "template.created"


# --- Cases 3, 4: errors stay in the pop-up ----------------------------------------------------


def test_a_taken_number_keeps_the_pop_up_open_with_the_entries(client: AppClient) -> None:
    first_template_id(client)
    response = client.post(
        "/templates/new", data=template_form(client, number="V-1", name="Zweite"), headers=HX
    )

    assert response.status_code == 422
    assert "<html" not in response.text
    assert 'id="popup-title"' in response.text and "hx-post" in response.text
    assert 'aria-invalid="true"' in response.text and field_error(response.text, "number")
    assert 'value="Zweite"' in response.text
    assert len(templates()) == 1


def test_an_empty_name_keeps_the_pop_up_open(client: AppClient) -> None:
    response = client.post("/templates/new", data=template_form(client, name=""), headers=HX)

    assert response.status_code == 422
    assert "<html" not in response.text
    assert field_error(response.text, "name")
    assert templates() == []


# --- Cases 6-8: edit in the pop-up ------------------------------------------------------------


def test_edit_pop_up_shows_the_stored_values(client: AppClient) -> None:
    make_template(client, number="V-1", name="Halter", selling_price="4,00")
    [stored] = templates()
    html = client.get(f"/templates/{stored.id}/edit", headers=HX).text

    assert 'value="V-1"' in html and 'value="Halter"' in html and 'value="4,00"' in html
    assert f'hx-post="/templates/{stored.id}"' in html


def test_saving_an_edit_in_the_pop_up_closes_it(client: AppClient) -> None:
    template_id = first_template_id(client)
    response = client.post(
        f"/templates/{template_id}",
        data=template_form(client, number="V-1", name="Neu"),
        headers=HX,
    )

    assert response.status_code == 204
    assert "popup-saved" in response.headers["hx-trigger"]
    assert templates()[0].name == "Neu"
    assert "Neu" in client.get(f"/templates/{template_id}").text
    assert log_actions()[-1] == "template.changed"


def test_an_edit_error_keeps_the_pop_up_open(client: AppClient) -> None:
    template_id = first_template_id(client)
    make_template(client, number="V-2", name="Anker")
    response = client.post(
        f"/templates/{template_id}",
        data=template_form(client, number="V-2", name="Halter"),
        headers=HX,
    )

    assert response.status_code == 422
    assert "<html" not in response.text and 'id="popup-title"' in response.text
    assert field_error(response.text, "number")
    assert templates()[0].number == "V-1"


# --- Case 13: without the pop-up everything works as before ------------------------------------


def test_without_the_pop_up_saving_still_redirects(client: AppClient) -> None:
    created = make_template(client, number="V-1", name="Halter")
    [stored] = templates()
    assert created.status_code == 303
    assert created.headers["location"] == f"/templates/{stored.id}"

    saved = client.post(
        f"/templates/{stored.id}",
        data=template_form(client, number="V-1", name="Neu"),
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == f"/templates/{stored.id}?saved=1"


# --- Cases 14, 15: rights and form token ------------------------------------------------------


def test_reader_gets_no_buttons_and_cannot_open_or_send(
    client: AppClient, reader: AppClient
) -> None:
    template_id = first_template_id(client)
    html = reader.get(f"/templates/{template_id}").text
    assert "hx-get" not in html
    for path in ("/templates/new", f"/templates/{template_id}/edit"):
        for headers in ({}, HX):
            assert reader.get(path, headers=headers).status_code == 403
    for path in ("/templates/new", f"/templates/{template_id}"):
        for headers in ({}, HX):
            response = reader.post(path, data=template_form(reader), headers=headers)
            assert response.status_code == 403
    assert len(templates()) == 1 and templates()[0].name == "Halter"


def test_without_login_nothing_is_shown_or_saved(client: AppClient) -> None:
    template_id = first_template_id(client)
    with AppClient(create_app()) as anonymous:
        for path in ("/templates/new", f"/templates/{template_id}/edit"):
            for headers in ({}, HX):
                get = anonymous.get(path, headers=headers, follow_redirects=False)
                assert get.status_code in (303, 401, 403)
                assert 'name="number"' not in get.text
        for path in ("/templates/new", f"/templates/{template_id}"):
            for headers in ({}, HX):
                post = anonymous.post(
                    path, data=template_form(anonymous), headers=headers, follow_redirects=False
                )
                assert post.status_code in (303, 401, 403)
    assert len(templates()) == 1


def test_a_wrong_form_token_is_refused_in_the_pop_up(client: AppClient) -> None:
    template_id = first_template_id(client)
    client.csrf_token = None
    for path in ("/templates/new", f"/templates/{template_id}"):
        response = client.post(
            path, data={**template_form(client), "csrf_token": "falsch"}, headers=HX
        )
        assert response.status_code == 403
    assert len(templates()) == 1 and templates()[0].name == "Halter"
