"""Alignment of the last screens to the common style (issue #171). All data is invented.

User edit, password reset, adding an attribute value and the two tables as pop-ups / shared
building block. The test case numbers refer to the comment on the issue; the phone view and the
real pop-ups are checked in tests/browser/test_style_alignment.py.
"""

import json
import re
from pathlib import Path

import pytest

from tests.auth import ADMIN_NAME, EMPLOYEE_NAME, AppClient, add_user, client_as, signed_in_client
from tests.popup_checks import HX, is_popup, opens_in_popup, saved_in_popup
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_templates import attributes, make_attribute
from tests.test_two_factor_reset import TEST_KEY, switch_on
from tests.test_users import OTHER_PASSWORD, admin_id, user_named
from tests.test_variant_bulk import make_setup, url

APP = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP / "locales" / "en.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("MAKERHUB_2FA_KEY", TEST_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def row_actions(html: str, name: str) -> str:
    """The action cell of the table row with this name."""
    row = re.search(rf"<tr>(?:(?!</tr>).)*>{name}<(?:(?!</tr>).)*</tr>", html, re.DOTALL)
    assert row, f"no row for {name}"
    return row.group(0)


# --- Case 1: user edit is a pop-up -------------------------------------------------------------


def test_user_list_has_icon_actions_that_open_pop_ups(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    html = client.get("/settings/users").text

    assert 'class="row-actions"' in html
    for address in (f"/settings/users/{user_id}", f"/settings/users/{user_id}/password"):
        assert opens_in_popup(html, address)
    assert 'class="icon-btn"' in row_actions(html, EMPLOYEE_NAME)
    # Two-factor is off: no reset action. Own row: nothing to change here.
    assert f"/settings/users/{user_id}/2fa-reset" not in html
    assert f"/settings/users/{admin_id()}/password" not in html
    switch_on(EMPLOYEE_NAME)
    assert opens_in_popup(
        client.get("/settings/users").text, f"/settings/users/{user_id}/2fa-reset"
    )


def test_user_edit_is_a_pop_up_and_saving_refreshes_the_list(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    popup = client.get(f"/settings/users/{user_id}", headers=HX)

    assert is_popup(popup.text) and EMPLOYEE_NAME in popup.text
    assert 'name="role"' in popup.text
    assert "password_repeat" not in popup.text  # the password has its own pop-up
    response = client.post(
        f"/settings/users/{user_id}", data={"role": "read_only", "active": "1"}, headers=HX
    )
    assert saved_in_popup(response)
    assert user_named(EMPLOYEE_NAME).role == "read_only"


def test_user_edit_errors_stay_in_the_pop_up(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    response = client.post(
        f"/settings/users/{user_id}", data={"role": "chef", "active": "1"}, headers=HX
    )

    assert response.status_code == 422 and is_popup(response.text)
    assert DE["user.error.choice"] in response.text
    assert user_named(EMPLOYEE_NAME).role == "employee"
    # The own account cannot lose its admin role (also not by a made-up request).
    own = client.post(
        f"/settings/users/{admin_id()}", data={"role": "employee", "active": "1"}, headers=HX
    )
    assert own.status_code == 422 and DE["user.error.self"] in own.text
    assert user_named(ADMIN_NAME).role == "admin"


def test_password_reset_is_a_pop_up(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    address = f"/settings/users/{user_id}/password"

    popup = client.get(address, headers=HX)

    assert is_popup(popup.text) and DE["user.reset.intro"] in popup.text
    short = {"password": "x", "password_repeat": "x"}
    failed = client.post(address, data=short, headers=HX)
    assert failed.status_code == 422 and is_popup(failed.text)
    ok = {"password": OTHER_PASSWORD, "password_repeat": OTHER_PASSWORD}
    assert saved_in_popup(client.post(address, data=ok, headers=HX))


def test_two_factor_reset_refreshes_the_list(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    switch_on(EMPLOYEE_NAME)

    response = client.post(f"/settings/users/{user_id}/2fa-reset", data={}, headers=HX)

    assert saved_in_popup(response)
    assert user_named(EMPLOYEE_NAME).totp_secret is None


# --- Case 2, 3, 6: rights, rules and the plain pages -------------------------------------------


def test_plain_pages_still_work_and_redirect_to_the_list(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    for address in (f"/settings/users/{user_id}", f"/settings/users/{user_id}/password"):
        page = client.get(address)
        assert (
            page.status_code == 200 and "<header" in page.text and 'name="csrf_token"' in page.text
        )
    saved = client.post(
        f"/settings/users/{user_id}",
        data={"role": "read_only", "active": "1"},
        follow_redirects=False,
    )
    assert saved.status_code == 303 and saved.headers["location"] == "/settings/users"
    failed = client.post(f"/settings/users/{user_id}/password", data={"password": "x"})
    assert failed.status_code == 422 and "<header" in failed.text
    assert client.get("/settings/users/999/password").status_code == 404


def test_password_page_is_for_admins_and_not_for_the_own_account(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        assert employee.get(f"/settings/users/{user_id}/password", headers=HX).status_code == 403
    own = client.post(
        f"/settings/users/{admin_id()}/password",
        data={"password": OTHER_PASSWORD, "password_repeat": OTHER_PASSWORD},
        headers=HX,
    )
    assert own.status_code == 422 and DE["user.error.self_password"] in own.text
    assert client.get(f"/settings/users/{admin_id()}/password", headers=HX).status_code == 409


# --- Case 4: attributes as a table -------------------------------------------------------------


def test_attribute_list_is_a_table_with_icon_actions(client: AppClient) -> None:
    make_attribute(client, "Farbe", "rot\nblau")
    make_attribute(client, "Größe", "S\nM")
    attribute_id = attributes()[0].id

    html = client.get("/attributes").text

    assert '<table class="data-table">' in html
    assert "blau (" in html and "rot (" in html
    assert 'class="row-actions"' in html and 'class="icon-btn"' in html
    for address in (f"/attributes/{attribute_id}/values/new", f"/attributes/{attribute_id}/delete"):
        assert opens_in_popup(html, address)
    assert DE["attribute.add_value_button"] not in html  # no text button in the rows
    assert 'aria-sort="ascending"' in html
    names = re.findall(r'<td class="cell-strong">(.*?)</td>', html)
    assert names == ["Farbe", "Größe"]
    descending = re.findall(
        r'<td class="cell-strong">(.*?)</td>', client.get("/attributes?sort=name&dir=desc").text
    )
    assert descending == ["Größe", "Farbe"]


def test_attribute_list_shows_no_actions_to_a_reader() -> None:
    with signed_in_client() as admin:
        make_attribute(admin)
    add_user("leser", "read_only")
    with client_as("leser") as reader:
        html = reader.get("/attributes").text
    assert "Farbe" in html and "icon-btn" not in html


def test_add_value_is_a_pop_up_and_errors_stay(client: AppClient) -> None:
    make_attribute(client)
    attribute_id = attributes()[0].id
    address = f"/attributes/{attribute_id}/values"

    popup = client.get(f"{address}/new", headers=HX)

    assert is_popup(popup.text) and "Farbe" in popup.text
    bad = client.post(address, data={"value": "rot", "abbreviation": "zz"}, headers=HX)
    assert bad.status_code == 422 and is_popup(bad.text)
    assert DE["attribute.error.value_taken"] in bad.text
    good = client.post(address, data={"value": "grün", "abbreviation": "gr"}, headers=HX)
    assert saved_in_popup(good)
    assert "grün (gr)" in client.get("/attributes").text


def test_add_value_plain_page(client: AppClient) -> None:
    make_attribute(client)
    attribute_id = attributes()[0].id
    address = f"/attributes/{attribute_id}/values"

    assert "<header" in client.get(f"{address}/new").text
    failed = client.post(address, data={"value": "", "abbreviation": ""})
    assert failed.status_code == 422 and "<header" in failed.text
    ok = client.post(address, data={"value": "gelb", "abbreviation": "ge"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/attributes"
    assert client.get("/attributes/999/values/new").status_code == 404


# --- Case 5: variants table --------------------------------------------------------------------


def test_new_icon_texts_exist_in_both_languages() -> None:
    for key in ("table.action.add_value", "table.action.password", "table.action.two_factor"):
        assert DE.get(key) and EN.get(key)


def test_variant_table_uses_the_shared_building_block(client: AppClient) -> None:
    setup = make_setup(client)

    for headers in ({}, HX):
        html = client.get(url(setup), headers=headers).text
        assert '<article class="data-card">' in html
        assert '<table class="data-table">' in html
        assert 'scope="col" class=""' in html  # header cells come from the shared macro
        assert 'name="number_0"' in html
