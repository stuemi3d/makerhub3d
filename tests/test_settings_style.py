"""Settings pages in the new style (issue #91, part 6b, test cases 1-8). All data is invented.

The test case numbers refer to the comment on the issue. The phone view and the real pop-up are
checked in tests/browser/test_settings_style.py.
"""

import re

import pytest

from tests.auth import (
    EMPLOYEE_NAME,
    OTHER_PASSWORD,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
    signed_in_client,
)
from tests.test_users import DE, new_user_form

HX = {"HX-Request": "true"}


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def names_in(html: str) -> list[str]:
    return re.findall(r'<td class="cell-strong">.*?>(.*?)</a></td>', html)


# --- Case 1: the user list is a sortable table with chips --------------------------------------


def test_user_list_is_a_sortable_table_with_chips(client: AppClient) -> None:
    add_user("anna", "employee")
    add_user("berta", "read_only")
    html = client.get("/settings/users").text

    assert '<table class="data-table">' in html
    assert 'class="chip chip-active"' in html
    assert names_in(html) == ["anna", "berta", "werkstatt"]
    assert names_in(client.get("/settings/users?sort=name&dir=desc").text) == [
        "werkstatt",
        "berta",
        "anna",
    ]
    assert 'aria-sort="descending"' in client.get("/settings/users?sort=name&dir=desc").text
    assert len(names_in(client.get("/settings/users?sort=role&dir=asc").text)) == 3


def test_user_list_ignores_a_bad_sort(client: AppClient) -> None:
    add_user("anna", "employee")

    html = client.get("/settings/users?sort=%27;drop&dir=sideways").text

    assert names_in(html) == ["anna", "werkstatt"]


def test_a_deactivated_user_shows_an_inactive_chip(client: AppClient) -> None:
    user_id = add_user("anna", "employee")
    client.post(f"/settings/users/{user_id}", data={"role": "employee", "active": "0"})

    html = client.get("/settings/users").text

    assert 'class="chip chip-inactive"' in html


# --- Cases 2 and 3: "create user" in the pop-up ------------------------------------------------


def test_create_user_link_opens_the_popup_and_the_form_comes_without_the_page(
    client: AppClient,
) -> None:
    page = client.get("/settings/users").text
    assert 'hx-get="/settings/users/new"' in page
    assert "data-popup-refresh" in page

    popup = client.get("/settings/users/new", headers=HX)
    assert popup.status_code == 200
    assert "<header" not in popup.text
    assert 'hx-post="/settings/users/new"' in popup.text
    assert "data-popup-close" in popup.text
    assert "<header" in client.get("/settings/users/new").text  # the normal page still works


def test_saving_in_the_popup_creates_the_user_and_asks_to_close(client: AppClient) -> None:
    response = client.post("/settings/users/new", data=new_user_form(), headers=HX)

    assert response.status_code == 204
    assert "popup-saved" in response.headers["HX-Trigger"]
    assert EMPLOYEE_NAME in client.get("/settings/users").text


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"username": ""}, "username"),
        ({"password_repeat": "etwas-anderes-langes"}, "password_repeat"),
        ({"username": "werkstatt"}, "username"),
    ],
)
def test_errors_in_the_popup_keep_it_open_with_the_message(
    client: AppClient, overrides: dict[str, str], field: str
) -> None:
    response = client.post("/settings/users/new", data=new_user_form(**overrides), headers=HX)

    assert response.status_code == 422
    assert "<header" not in response.text
    assert f'id="{field}-error"' in response.text
    assert OTHER_PASSWORD not in response.text


def test_create_user_without_popup_still_redirects(client: AppClient) -> None:
    response = client.post("/settings/users/new", data=new_user_form(), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/settings/users"


# --- Case 4: the edit page is made of cards ----------------------------------------------------


def test_user_edit_page_shows_cards_and_keeps_its_functions(client: AppClient) -> None:
    user_id = add_user("anna", "employee")
    html = client.get(f"/settings/users/{user_id}").text

    assert html.count('class="detail-card"') >= 3
    assert f'action="/settings/users/{user_id}/password"' in html

    saved = client.post(f"/settings/users/{user_id}", data={"role": "read_only", "active": "1"})
    assert saved.status_code == 200
    assert DE["user.saved"] in saved.text


# --- Case 5: backups ---------------------------------------------------------------------------


def test_backup_list_is_a_table_with_a_kind_chip(client: AppClient) -> None:
    response = client.post("/settings/backups", data={})
    html = response.text

    assert DE["backup.created"] in html
    assert '<table class="data-table">' in html
    assert re.search(r'<span class="chip chip-\w+">' + re.escape(DE["backup.kind.manual"]), html)
    assert "/download" in html


# --- Case 6: the logs --------------------------------------------------------------------------


def test_both_log_tabs_use_the_table_card(client: AppClient) -> None:
    client.post("/settings/backups", data={})

    for path in ("/settings/log", "/settings/log/logins"):
        html = client.get(path).text
        assert '<table class="data-table">' in html
        assert 'class="data-card"' in html
    assert 'aria-current="page"' in client.get("/settings/log/logins").text


# --- Case 7: values and appearance are cards ---------------------------------------------------


def test_settings_and_appearance_are_cards(client: AppClient) -> None:
    values = client.get("/settings").text
    assert values.count('class="detail-card"') >= 2
    assert 'name="electricity_price"' in values
    appearance = client.get("/settings/appearance").text
    assert 'class="detail-card"' in appearance
    assert 'id="theme-dark"' in appearance


# --- Case 8: rights ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/settings",
        "/settings/users",
        "/settings/users/new",
        "/settings/backups",
        "/settings/log",
        "/settings/log/logins",
    ],
)
def test_only_admins_see_the_admin_pages(client: AppClient, path: str) -> None:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        assert reader.get(path).status_code == 403
        assert reader.get(path, headers=HX).status_code == 403
        assert reader.get("/settings/appearance").status_code == 200
    with AppClient(client.app) as visitor:
        response = visitor.get(path, follow_redirects=False)
        assert response.status_code in (302, 303)
        assert "/login" in response.headers["location"]
