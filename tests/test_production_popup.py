"""Production form: stale numbers in empty rows, the pop-up and the order on the page
(issue #138, test cases 1-10). All data is invented. The test case numbers refer to the comment
on the issue.
"""

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Production
from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_booking import (
    field_error,
    form,
    productions,
    send,
    setup_prints,
)
from tests.test_stock import add_article, db

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


def alloc_value(html: str, number: int) -> str | None:
    match = re.search(rf'name="alloc_{number}"[^>]*value="([^"]*)"', html)
    return match.group(1) if match else None


# --- Cases 1-4: numbers that are left over from an earlier preview -----------------------------


def test_an_empty_row_with_an_old_cost_number_still_counts_as_empty(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    data = form(
        [6, 7],
        [(article_id, "5")],
        action="preview",
        alloc_1="9,99",
        alloc_2="2,90",
        preview_key="alt",
    )

    response = send(prepared_client, data)

    assert response.status_code == 200
    assert alloc_value(response.text, 1) == "7,25"
    assert field_error(response.text, "article_2") is None


def test_the_same_article_twice_and_then_changing_again_and_again(
    prepared_client: AppClient,
) -> None:
    first, second = add_article("A-1"), add_article("A-2")

    twice = send(prepared_client, form([6], [(first, "2"), (first, "3")], action="preview"))
    assert twice.status_code == 422
    assert field_error(twice.text, "article_2") == DE["production.error.article_twice"]
    assert productions() == []

    # Row 2 is emptied, but the old numbers stay in the form, as a browser would send them.
    stale = {"alloc_1": "1,00", "alloc_2": "2,00", "preview_key": "alt"}
    for quantity in ("2", "3", "4", "5"):
        again = send(prepared_client, form([6], [(first, quantity)], action="preview", **stale))
        assert again.status_code == 200
        assert alloc_value(again.text, 1) == "4,50"
    other = send(prepared_client, form([6], [(first, "2"), (second, "3")], action="preview"))
    assert other.status_code == 200
    assert alloc_value(other.text, 1) == "1,80" and alloc_value(other.text, 2) == "2,70"


def test_after_an_error_there_are_no_old_costs_and_no_old_total(prepared_client: AppClient) -> None:
    first = add_article("A-1")
    ok = send(prepared_client, form([6], [(first, "2")], action="preview"))
    assert alloc_value(ok.text, 1) == "4,50" and DE["production.preview.total"] in ok.text

    bad = send(
        prepared_client,
        form(
            [6],
            [(first, "0")],
            action="preview",
            alloc_1="4,50",
            preview_key=re.search(r'name="preview_key"[^>]*value="([^"]+)"', ok.text).group(1),
        ),
    )

    assert bad.status_code == 422
    assert 'name="alloc_1"' not in bad.text
    assert DE["production.preview.total"] not in bad.text
    assert re.search(r'name="preview_key"[^>]*value=""', bad.text)


def test_hand_made_costs_count_only_for_rows_with_an_article(prepared_client: AppClient) -> None:
    article_id = add_article()
    key = re.search(
        r'name="preview_key"[^>]*value="([^"]+)"',
        send(prepared_client, form([6], [(article_id, "2")], action="preview")).text,
    ).group(1)

    response = send(
        prepared_client,
        form([6], [(article_id, "2")], preview_key=key, alloc_1="4,50", alloc_3="1,00"),
    )

    assert response.status_code == 303
    assert productions()[0].results[0].allocated_cost == productions()[0].total_cost


def test_a_wrong_hand_made_split_keeps_the_typed_values_in_the_form(
    prepared_client: AppClient,
) -> None:
    first, second = add_article("A-1"), add_article("A-2")
    data = form([6], [(first, "2"), (second, "2")])
    key = re.search(
        r'name="preview_key"[^>]*value="([^"]+)"',
        send(prepared_client, {**data, "action": "preview"}).text,
    ).group(1)

    response = send(
        prepared_client, {**data, "preview_key": key, "alloc_1": "4,00", "alloc_2": "1,00"}
    )

    assert response.status_code == 422
    assert DE["production.error.alloc_sum"] in response.text
    assert alloc_value(response.text, 1) == "4,00" and alloc_value(response.text, 2) == "1,00"
    with db() as session:
        assert session.scalars(select(Production)).all() == []


def test_a_refused_booking_for_another_reason_shows_no_old_costs(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    ok = send(prepared_client, form([6], [(article_id, "2")], action="preview"))
    key = re.search(r'name="preview_key"[^>]*value="([^"]+)"', ok.text).group(1)

    response = send(
        prepared_client, form([6], [(article_id, "0")], preview_key=key, alloc_1="4,50")
    )

    assert response.status_code == 422
    assert field_error(response.text, "quantity_1")
    assert 'name="alloc_1"' not in response.text
    assert re.search(r'name="preview_key"[^>]*value=""', response.text)


def test_adding_a_row_hides_the_split_until_the_next_preview(prepared_client: AppClient) -> None:
    article_id = add_article()
    ok = send(prepared_client, form([6], [(article_id, "2")], action="preview"))
    key = re.search(r'name="preview_key"[^>]*value="([^"]+)"', ok.text).group(1)

    response = send(
        prepared_client,
        form([6], [(article_id, "2")], action="add_row", preview_key=key, alloc_1="4,50"),
    )

    assert response.status_code == 200
    assert 'name="alloc_1"' not in response.text
    assert DE["production.preview.total"] not in response.text
    assert response.text.count('name="article_') == 4


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


def test_preview_add_row_and_errors_stay_in_the_popup(prepared_client: AppClient) -> None:
    article_id = add_article()

    preview = popup_send(prepared_client, form([6], [(article_id, "2")], action="preview"))
    add_row = popup_send(prepared_client, form([6], [(article_id, "2")], action="add_row"))
    error = popup_send(prepared_client, form([6], [(article_id, "0")], action="preview"))

    for response in (preview, add_row, error):
        assert "<html" not in response.text and 'id="popup-title"' in response.text
        assert 'hx-post="/production/new"' in response.text
    assert (preview.status_code, add_row.status_code, error.status_code) == (200, 200, 422)
    assert productions() == []


def test_booking_in_the_popup_goes_to_the_list_with_the_message(prepared_client: AppClient) -> None:
    article_id = add_article()

    response = popup_send(prepared_client, form([6], [(article_id, "2")]))

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
        assert popup_send(reader, form([6], [(article_id, "2")])).status_code == 403
    prepared_client.csrf_token = None
    response = prepared_client.post(
        "/production/new", data=form([6], [(article_id, "2")]), headers=HX
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
