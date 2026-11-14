"""Variants and all combinations in pop-ups (issue #87, part B, test cases 17-30).

All data is invented. The test case numbers refer to the comment on the issue. Opening,
closing, the number suggestion and the phone view are checked in
tests/browser/test_variant_popups.py; here the server side is checked.
"""

import json
import re

import pytest

from app.main import create_app
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.test_variant_bulk import form_from, numbers_in
from tests.test_variants import (
    DE,
    Setup,
    article_count,
    log_entries,
    make_setup,
    variant_form,
)

HX = {"HX-Request": "true"}


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


@pytest.fixture
def setup(client: AppClient) -> Setup:
    return make_setup(client)


@pytest.fixture
def reader(client: AppClient, setup: Setup) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


def one(setup: Setup) -> str:
    return f"/templates/{setup.template_id}/variants/new"


def all_(setup: Setup) -> str:
    return f"/templates/{setup.template_id}/variants/all"


def is_fragment(html: str) -> bool:
    return "<html" not in html and "<header" not in html and "<form" in html


# --- Case 17, 21: the buttons open the pop-up and the page is complete without it --------------


def test_template_buttons_open_the_pop_ups_and_keep_a_normal_address(
    client: AppClient, setup: Setup
) -> None:
    html = client.get(f"/templates/{setup.template_id}").text

    for path in (one(setup), all_(setup)):
        link = re.search(rf'<a [^>]*href="{re.escape(path)}"[^>]*>', html)
        assert link, path
        assert f'hx-get="{path}"' in link.group(0)
        assert "data-popup" in link.group(0)
    assert '<dialog id="popup"' in html
    assert re.search(r'<div id="template-variants"[^>]*data-popup-refresh', html)


def test_single_variant_pop_up_is_only_the_form(client: AppClient, setup: Setup) -> None:
    popup = client.get(one(setup), headers=HX)
    page = client.get(one(setup))

    assert popup.status_code == page.status_code == 200
    assert is_fragment(popup.text)
    assert "<html" in page.text and "topbar" in page.text
    assert "HX-Request" in popup.headers["vary"]
    for field in ("number", "name", f"attr_{setup.colour_id}", f"attr_{setup.size_id}"):
        assert f'name="{field}"' in popup.text and f'name="{field}"' in page.text
    assert "hx-post" in popup.text and "hx-post" not in page.text
    assert re.search(r'name="csrf_token" value="[^"]+"', popup.text)
    assert 'id="popup-title"' in popup.text
    # The number suggestion needs the prefix and the abbreviations in the pop-up as well.
    assert "data-number-prefix" in popup.text
    assert "data-abbreviation" in popup.text


def test_bulk_pop_up_is_only_the_form_with_the_table(client: AppClient, setup: Setup) -> None:
    popup = client.get(all_(setup), headers=HX)
    page = client.get(all_(setup))

    assert popup.status_code == page.status_code == 200
    assert is_fragment(popup.text)
    assert "<html" in page.text and "topbar" in page.text
    assert "HX-Request" in popup.headers["vary"]
    assert len(numbers_in(popup.text)) == len(numbers_in(page.text)) == 4
    assert "hx-post" in popup.text and "hx-post" not in page.text
    assert 'id="popup-title"' in popup.text
    assert 'class="data-scroll"' in popup.text


# --- Case 18, 22: saving ----------------------------------------------------------------------


def test_saving_a_variant_answers_without_a_page_and_shows_it_in_the_list(
    client: AppClient, setup: Setup
) -> None:
    response = client.post(one(setup), data=variant_form(setup), headers=HX)

    assert response.status_code == 204
    assert response.content == b""
    assert json.loads(response.headers["hx-trigger"]) == {"popup-saved": {}}
    assert article_count() == 1
    assert len(log_entries("article.variant_created")) == 1
    assert "3D-0042-RT" in client.get(f"/templates/{setup.template_id}").text


def test_saving_all_combinations_answers_without_a_page(client: AppClient, setup: Setup) -> None:
    html = client.get(all_(setup), headers=HX).text

    response = client.post(all_(setup), data=form_from(html), headers=HX)

    assert response.status_code == 204
    assert json.loads(response.headers["hx-trigger"]) == {"popup-saved": {}}
    assert article_count() == 4
    assert len(log_entries("article.variant_created")) == 4


# --- Case 19, 23: errors ----------------------------------------------------------------------


def test_variant_errors_stay_in_the_pop_up_keep_the_input_and_save_nothing(
    client: AppClient, setup: Setup
) -> None:
    client.post(one(setup), data=variant_form(setup), headers=HX)

    same_combination = client.post(
        one(setup), data=variant_form(setup, number="3D-0043", name="Doppelt"), headers=HX
    )
    same_number = client.post(
        one(setup), data=variant_form(setup, colour="blau", name="Andere"), headers=HX
    )
    missing_value = client.post(
        one(setup), data=variant_form(setup, colour=None, number="3D-0044", name="Ohne"), headers=HX
    )
    empty_number = client.post(one(setup), data=variant_form(setup, number=""), headers=HX)

    for response in (same_combination, same_number, missing_value, empty_number):
        assert response.status_code == 422
        assert is_fragment(response.text)
        assert "hx-post" in response.text
    assert DE["variant.error.combination_taken"] in same_combination.text
    assert 'value="Doppelt"' in same_combination.text
    assert 'aria-invalid="true"' in same_number.text
    assert 'value="Andere"' in same_number.text
    assert 'aria-invalid="true"' in missing_value.text
    assert 'aria-invalid="true"' in empty_number.text
    assert article_count() == 1


def test_bulk_errors_stay_in_the_pop_up_and_nothing_is_saved(
    client: AppClient, setup: Setup
) -> None:
    html = client.get(all_(setup), headers=HX).text

    empty = client.post(all_(setup), data=form_from(html, number_0=""), headers=HX)
    twice = client.post(
        all_(setup), data=form_from(html, number_0="X-1", number_1="X-1"), headers=HX
    )

    for response in (empty, twice):
        assert response.status_code == 422
        assert is_fragment(response.text)
        assert "hx-post" in response.text
        assert re.search(r'<small id="number_\d+-error"', response.text)
    assert 'value="X-1"' in twice.text
    assert article_count() == 0


def test_bulk_pop_up_without_news_shows_the_hint_and_no_button(
    client: AppClient, setup: Setup
) -> None:
    html = client.get(all_(setup), headers=HX).text
    client.post(all_(setup), data=form_from(html), headers=HX)

    popup = client.get(all_(setup), headers=HX)

    assert DE["variant.bulk.nothing_new"] in popup.text
    assert not re.search(r'<button type="submit"', popup.text)


# --- Case 25, 26: close only on purpose, normal pages without JavaScript ----------------------


def test_pop_ups_have_a_cancel_button_and_the_pages_a_cancel_link(
    client: AppClient, setup: Setup
) -> None:
    for path in (one(setup), all_(setup)):
        assert "data-popup-close" in client.get(path, headers=HX).text
        page = client.get(path).text
        assert "data-popup-close" not in page
        assert f'href="/templates/{setup.template_id}"' in page


def test_plain_pages_still_redirect_and_show_errors_on_the_page(
    client: AppClient, setup: Setup
) -> None:
    saved = client.post(one(setup), data=variant_form(setup), follow_redirects=False)
    failed = client.post(one(setup), data=variant_form(setup, number=""))
    html = client.get(all_(setup)).text
    bulk_failed = client.post(all_(setup), data=form_from(html, number_0=""))
    bulk_saved = client.post(
        all_(setup), data=form_from(html, number_0="Z-1"), follow_redirects=False
    )

    assert saved.status_code == 303
    assert saved.headers["location"].startswith("/articles/")
    assert failed.status_code == 422
    assert "<html" in failed.text and "hx-post" not in failed.text
    assert bulk_saved.status_code == 303
    assert bulk_saved.headers["location"] == f"/templates/{setup.template_id}?saved=1"
    assert bulk_failed.status_code == 422
    assert "<html" in bulk_failed.text and "hx-post" not in bulk_failed.text


# --- Case 27: rights, login, token, state -----------------------------------------------------


def test_reader_gets_no_buttons_and_cannot_open_or_send(reader: AppClient, setup: Setup) -> None:
    html = reader.get(f"/templates/{setup.template_id}").text
    assert "/variants/" not in html
    assert "hx-get" not in html
    for path in (one(setup), all_(setup)):
        for headers in ({}, HX):
            assert reader.get(path, headers=headers).status_code == 403
            assert reader.post(path, data=variant_form(setup), headers=headers).status_code == 403
    assert article_count() == 0


def test_without_login_nothing_is_shown_or_saved(setup: Setup) -> None:
    with AppClient(create_app()) as anonymous:
        for path in (one(setup), all_(setup)):
            for headers in ({}, HX):
                get = anonymous.get(path, headers=headers, follow_redirects=False)
                post = anonymous.post(
                    path, data=variant_form(setup), headers=headers, follow_redirects=False
                )
                assert get.status_code in (303, 401, 403)
                assert post.status_code in (303, 401, 403)
                assert 'name="number' not in get.text
    assert article_count() == 0


def test_employee_may_use_the_pop_ups(client: AppClient, setup: Setup) -> None:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        assert employee.get(one(setup), headers=HX).status_code == 200
        assert employee.post(one(setup), data=variant_form(setup), headers=HX).status_code == 204


def test_saving_without_a_form_token_is_refused(client: AppClient, setup: Setup) -> None:
    html = client.get(all_(setup)).text
    client.csrf_token = None

    one_response = client.post(
        one(setup), data={**variant_form(setup), "csrf_token": "falsch"}, headers=HX
    )
    all_response = client.post(
        all_(setup), data={**form_from(html), "csrf_token": "falsch"}, headers=HX
    )

    assert one_response.status_code == all_response.status_code == 403
    assert article_count() == 0


def test_unusable_templates_still_give_409_in_the_pop_up(client: AppClient, setup: Setup) -> None:
    client.post(f"/templates/{setup.template_id}/deactivate", data={})

    for path in (one(setup), all_(setup)):
        for headers in ({}, HX):
            assert client.get(path, headers=headers).status_code == 409
    assert client.get("/templates/9999/variants/new", headers=HX).status_code == 404


# --- Case 28: safe output ---------------------------------------------------------------------


def test_input_is_escaped_in_the_pop_up_and_nothing_is_loaded_from_other_servers(
    client: AppClient, setup: Setup
) -> None:
    response = client.post(
        one(setup),
        data=variant_form(setup, number="", name="<script>alert(1)</script>"),
        headers=HX,
    )

    assert response.status_code == 422
    assert "<script>alert(1)</script>" not in response.text
    for path in (one(setup), all_(setup), f"/templates/{setup.template_id}"):
        assert not re.findall(r'(?:src|href)="(?:https?:)?//', client.get(path).text)
