"""Production form: the pop-up and the order on the page (issues #138 and #140).

All data is invented. The test case numbers refer to the comment on issue #138.
"""

import json
import re
from pathlib import Path

import pytest

from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_booking import (
    form,
    productions,
    setup_prints,
)
from tests.test_stock import add_article

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
HX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def prepared_client(client: AppClient) -> AppClient:
    setup_prints(client)
    return client


def popup_send(client: AppClient, data: dict[str, object]):
    return client.post("/production/new", data=data, headers=HX, follow_redirects=False)


# --- Cases 5-8: the pop-up ---------------------------------------------------------------------


def test_the_form_comes_as_a_popup_fragment_for_htmx_and_as_a_page_otherwise(
    prepared_client: AppClient,
) -> None:
    fragment = prepared_client.get("/production/new?prints=6", headers=HX)
    page = prepared_client.get("/production/new?prints=6")

    assert fragment.status_code == 200
    assert "<html" not in fragment.text and 'aria-label="breadcrumb"' not in fragment.text
    assert 'id="popup-title"' in fragment.text
    assert 'hx-post="/production/new"' in fragment.text
    assert 'hx-target="#popup-body"' in fragment.text
    assert "data-popup-close" in fragment.text
    assert "<html" in page.text and 'aria-label="breadcrumb"' in page.text
    assert "hx-post" not in page.text and "data-popup-close" not in page.text
    assert 'name="prints"' in fragment.text and 'name="csrf_token"' in fragment.text


def test_the_button_form_opens_the_popup_and_still_works_without_script(
    prepared_client: AppClient,
) -> None:
    html = prepared_client.get("/production").text

    tag = re.search(r'<form id="select-form"[^>]*>', html).group(0)
    assert 'method="get"' in tag and 'action="/production/new"' in tag
    assert 'hx-get="/production/new"' in tag
    assert 'hx-target="#popup-body"' in tag
    assert "data-popup" in tag


def test_preview_and_errors_stay_in_the_popup(prepared_client: AppClient) -> None:
    article_id = add_article()

    preview = popup_send(prepared_client, form([6], (article_id, "2"), action="preview"))
    error = popup_send(prepared_client, form([6], (article_id, "0"), action="preview"))

    for response in (preview, error):
        assert "<html" not in response.text and 'id="popup-title"' in response.text
        assert 'hx-post="/production/new"' in response.text
    assert (preview.status_code, error.status_code) == (200, 422)
    assert productions() == []


def test_booking_in_the_popup_goes_to_the_list_with_the_message(prepared_client: AppClient) -> None:
    article_id = add_article()

    response = popup_send(prepared_client, form([6], (article_id, "2")))

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/production/list?booked=1"
    assert len(productions()) == 1
    assert DE["production.booked"] in prepared_client.get("/production/list?booked=1").text


def test_rights_and_form_token_are_the_same_in_the_popup(prepared_client: AppClient) -> None:
    article_id = add_article()
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        reader.app.state.bambuddy_transport = prepared_client.app.state.bambuddy_transport
        assert reader.get("/production/new?prints=6", headers=HX).status_code == 403
        assert popup_send(reader, form([6], (article_id, "2"))).status_code == 403
    prepared_client.csrf_token = None
    response = prepared_client.post(
        "/production/new", data=form([6], (article_id, "2")), headers=HX
    )
    assert response.status_code == 403
    assert productions() == []


# --- Cases 9-10: the order on the page ---------------------------------------------------------


def test_tabs_first_then_the_row_with_title_and_button_then_the_hint_then_the_table(
    prepared_client: AppClient,
) -> None:
    html = prepared_client.get("/production").text

    positions = [
        html.index('class="area-tabs"'),
        html.index('<div class="page-head">'),
        html.index('class="page-actions"'),
        html.index(DE["production.create.hint"]),
        html.index('class="data-card"'),
    ]
    assert positions == sorted(positions)
    head = re.search(r'<div class="page-head">.*?</div>\s*</div>', html, re.S).group(0)
    assert "<h1>" in head and "data-requires-checked" in head


def test_the_productions_tab_has_the_same_order(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/list").text

    assert html.index('class="area-tabs"') < html.index('<div class="page-head">')
    assert html.index('<div class="page-head">') < html.index(DE["production.list.empty"])
