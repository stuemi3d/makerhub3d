"""Deleting and confirming as pop-ups everywhere (issue #169). All data is invented.

The seven places: device, attribute, template, article, article "delete for good", cancelling a
production, resetting two-factor login. Each opens a pop-up with the same build, never a
browser dialog. The plain pages (without HTMX) still work. The test case numbers refer to the
comment on the issue; opening, closing and the phone view are in tests/browser/.
"""

import json
import re
from pathlib import Path

import pytest

from tests.auth import ADMIN_NAME, EMPLOYEE_NAME, AppClient, add_user, client_as
from tests.popup_checks import HX, is_popup, leaves_to, opens_in_popup, saved_in_popup
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_device_pages import add as add_device
from tests.test_device_pages import printer_form, stored_devices
from tests.test_production_booking import form, productions, send, setup_prints
from tests.test_stock import add_article, add_movement
from tests.test_templates import attributes, make_attribute, make_template, templates
from tests.test_two_factor_reset import TEST_KEY, switch_on, user_named

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
DE = json.loads((APP / "locales" / "de.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("MAKERHUB_2FA_KEY", TEST_KEY)


# --- Case 1: no browser dialog anywhere --------------------------------------------------------


def test_no_template_and_no_script_uses_a_browser_dialog() -> None:
    for path in (APP / "templates").glob("*.html"):
        assert "data-confirm" not in path.read_text(encoding="utf-8"), path.name
    script = (APP / "static" / "app.js").read_text(encoding="utf-8")
    assert "confirm(" not in script and "alert(" not in script


# --- Device (case 1-3, 6) ----------------------------------------------------------------------


def test_device_delete_is_a_pop_up_and_confirming_refreshes_the_list(client: AppClient) -> None:
    add_device(client, printer_form())
    device = stored_devices()[0]
    address = f"/settings/devices/{device.id}/delete"
    assert opens_in_popup(client.get("/settings/devices").text, address)

    popup = client.get(address, headers=HX)

    assert is_popup(popup.text) and "Drucker A" in popup.text
    assert len(stored_devices()) == 1
    response = client.post(address, data={}, headers=HX)
    assert saved_in_popup(response)
    assert stored_devices() == []


# --- Attribute ---------------------------------------------------------------------------------


def test_attribute_delete_is_a_pop_up_and_confirming_refreshes_the_list(
    client: AppClient,
) -> None:
    make_attribute(client)
    attribute = attributes()[0]
    address = f"/attributes/{attribute.id}/delete"
    assert opens_in_popup(client.get("/attributes").text, address)

    popup = client.get(address, headers=HX)

    assert is_popup(popup.text) and "Farbe" in popup.text
    assert saved_in_popup(client.post(address, data={}, headers=HX))
    assert attributes() == []


def test_a_used_attribute_says_so_in_the_pop_up_and_has_no_button(client: AppClient) -> None:
    make_attribute(client)
    make_template(client, **{f"attr_{attributes()[0].id}": "1"})
    address = f"/attributes/{attributes()[0].id}/delete"

    popup = client.get(address, headers=HX).text

    assert DE["attribute.delete.blocked"] in popup
    assert "hx-post=" not in popup
    assert client.post(address, data={}, headers=HX).status_code == 409


# --- Template ----------------------------------------------------------------------------------


def test_template_delete_is_a_pop_up_and_leads_to_the_list(client: AppClient) -> None:
    make_attribute(client)
    make_template(client, **{f"attr_{attributes()[0].id}": "1"})
    template_id = templates()[0].id
    address = f"/templates/{template_id}/delete"
    assert opens_in_popup(client.get(f"/templates/{template_id}").text, address)

    popup = client.get(address, headers=HX)

    assert is_popup(popup.text) and "Halter A" in popup.text
    # The page behind the pop-up is gone afterwards, so the browser goes to the list.
    assert leaves_to(client.post(address, data={}, headers=HX), "/templates")
    assert templates() == []


# --- Article and "delete for good" -------------------------------------------------------------


def test_article_delete_is_a_pop_up_and_leads_to_the_list(client: AppClient) -> None:
    article_id = add_article("A-1", "Kabelhalter")
    address = f"/articles/{article_id}/delete"
    assert opens_in_popup(client.get(f"/articles/{article_id}").text, address)

    popup = client.get(address, headers=HX)

    assert is_popup(popup.text) and "A-1" in popup.text
    assert DE["article.delete.confirm"] in popup.text
    assert leaves_to(client.post(address, data={}, headers=HX), "/articles")
    assert client.get(f"/articles/{article_id}").status_code == 404


def purge_setup() -> int:
    article_id = add_article("3D-0001", "Kabelhalter")
    add_movement(article_id, 10)
    return article_id


def test_purge_is_a_pop_up_that_asks_for_the_number(client: AppClient) -> None:
    article_id = purge_setup()
    address = f"/articles/{article_id}/purge"
    assert opens_in_popup(client.get(f"/articles/{article_id}").text, address)

    popup = client.get(address, headers=HX).text

    assert is_popup(popup)
    assert DE["article.purge.irreversible"] in popup
    assert f"{DE['article.purge.count.stock_movements']}: 1" in popup
    assert 'name="number"' in popup


def test_a_wrong_number_shows_the_error_inside_the_pop_up(client: AppClient) -> None:
    article_id = purge_setup()

    response = client.post(f"/articles/{article_id}/purge", data={"number": "falsch"}, headers=HX)

    assert response.status_code == 422
    assert is_popup(response.text) and DE["article.purge.mismatch"] in response.text


def test_the_right_number_deletes_for_good_and_leads_to_the_list(client: AppClient) -> None:
    article_id = purge_setup()

    response = client.post(f"/articles/{article_id}/purge", data={"number": "3D-0001"}, headers=HX)

    assert leaves_to(response, "/articles?purged=1")
    assert client.get(f"/articles/{article_id}").status_code == 404


# --- Cancel a production -----------------------------------------------------------------------


@pytest.fixture
def prepared_client(client: AppClient) -> AppClient:
    setup_prints(client)
    return client


def test_cancel_production_is_a_pop_up_from_the_list_and_the_detail(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    send(prepared_client, form([6, 7], (article_id, "10")))
    address = "/production/1/cancel"
    assert opens_in_popup(prepared_client.get("/production/list").text, address)
    assert opens_in_popup(prepared_client.get("/production/1").text, address)

    popup = prepared_client.get(address, headers=HX)

    assert is_popup(popup.text) and DE["production.cancel.confirm"] in popup.text
    assert not productions()[0].cancelled
    response = prepared_client.post(address, data={}, headers=HX)
    assert leaves_to(response, "/production/list?cancelled=1")
    assert productions()[0].cancelled


def test_cancelling_twice_shows_the_error_inside_the_pop_up(prepared_client: AppClient) -> None:
    send(prepared_client, form([6], (add_article(), "5")))
    prepared_client.post("/production/1/cancel", data={}, headers=HX)

    response = prepared_client.post("/production/1/cancel", data={}, headers=HX)

    assert response.status_code == 422
    assert is_popup(response.text) and 'role="alert"' in response.text


# --- Reset two-factor login --------------------------------------------------------------------


def test_two_factor_reset_is_a_pop_up_and_the_user_page_reloads(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    switch_on(EMPLOYEE_NAME)
    address = f"/settings/users/{user_id}/2fa-reset"
    assert opens_in_popup(client.get(f"/settings/users/{user_id}").text, address)

    popup = client.get(address, headers=HX)

    assert is_popup(popup.text) and DE["user.two_factor.reset.confirm"] in popup.text
    assert user_named(EMPLOYEE_NAME).totp_secret is not None
    response = client.post(address, data={}, headers=HX)
    assert leaves_to(response, f"/settings/users/{user_id}?tfa_reset=1")
    assert user_named(EMPLOYEE_NAME).totp_secret is None


# --- Case 4, 5: the plain pages still work and the rules stay ---------------------------------


def test_without_htmx_the_pages_are_complete_pages(client: AppClient) -> None:
    article_id = add_article("A-1", "Kabelhalter")

    page = client.get(f"/articles/{article_id}/delete").text

    assert "<html" in page and "hx-post=" not in page
    assert f'action="/articles/{article_id}/delete"' in page


def test_the_pop_up_answers_vary_by_the_htmx_header(client: AppClient) -> None:
    article_id = add_article("A-1", "Kabelhalter")

    assert "HX-Request" in client.get(f"/articles/{article_id}/delete", headers=HX).headers.get(
        "Vary", ""
    )


def test_pop_up_forms_carry_the_form_token(client: AppClient) -> None:
    article_id = add_article("A-1", "Kabelhalter")

    popup = client.get(f"/articles/{article_id}/delete", headers=HX).text

    assert re.search(r'name="csrf_token" value="[^"]+"', popup)


def test_a_pop_up_post_without_the_form_token_is_refused(client: AppClient) -> None:
    article_id = add_article("A-1", "Kabelhalter")
    client.csrf_token = None

    response = client.post(f"/articles/{article_id}/delete", data={}, headers=HX)

    assert response.status_code == 403
    assert client.get(f"/articles/{article_id}").status_code in (200, 403)


def test_the_two_factor_question_is_for_admins_only_and_only_where_there_is_something(
    client: AppClient,
) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    address = f"/settings/users/{user_id}/2fa-reset"
    # Two-factor login is off for this user: nothing to reset.
    assert client.get(address, headers=HX).status_code == 409
    with client_as(EMPLOYEE_NAME) as employee:
        switch_on(EMPLOYEE_NAME)
        assert client.get(address, headers=HX).status_code == 200
        # An employee may not even ask.
        assert employee.get(address, headers=HX).status_code == 403
    # An admin cannot reset the own login this way.
    own_id = user_named(ADMIN_NAME).id
    assert client.get(f"/settings/users/{own_id}/2fa-reset", headers=HX).status_code == 409
