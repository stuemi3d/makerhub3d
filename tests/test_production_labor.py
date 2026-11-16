"""Production form: labour costs (issue #134). All data is invented; Bambuddy answers come
from a mock. The test case numbers refer to the comment on the issue.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import AppSettings
from tests.auth import AppClient
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_booking import field_error, form, productions, send, setup_prints
from tests.test_stock import add_article, db

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def prepared_client(client: AppClient) -> AppClient:
    setup_prints(client)
    with db() as session, session.begin():
        session.get(AppSettings, 1).labour_rate = D("30.00")
    return client


def input_value(html: str, name: str) -> str | None:
    match = re.search(rf'<input\b[^>]*name="{name}"[^>]*value="([^"]*)"', html)
    return match.group(1) if match else None


# --- Case 1: the fields ------------------------------------------------------------------------


def test_the_form_has_an_empty_time_field_and_the_rate_from_the_settings(
    prepared_client: AppClient,
) -> None:
    html = prepared_client.get("/production/new?prints=6").text

    assert input_value(html, "labor_minutes") == ""
    assert input_value(html, "labor_rate") == "30"
    assert DE["production.field.labor_minutes"] in html
    assert DE["production.field.labor_rate"] in html


# --- Cases 2-3: preview and booking ------------------------------------------------------------


def test_the_preview_shows_print_labour_and_total_separately(prepared_client: AppClient) -> None:
    article_id = add_article()
    data = form([6], (article_id, "5"), action="preview", labor_minutes="90", labor_rate="30")

    response = send(prepared_client, data)

    assert response.status_code == 200
    html = response.text
    assert DE["production.preview.print"] in html
    assert DE["production.preview.labor"] in html and "45" in html
    assert DE["production.preview.total"] in html and "49,5" in html
    assert productions() == []


def test_booking_stores_total_minutes_rate_and_labour_cost(prepared_client: AppClient) -> None:
    article_id = add_article()
    data = form([6], (article_id, "5"), labor_minutes="90", labor_rate="30")

    assert send(prepared_client, data).status_code == 303

    (item,) = productions()
    assert (item.labor_minutes, item.labor_rate, item.labor_cost) == (D("90"), D("30"), D("45.00"))
    assert item.total_cost == D("49.50")
    assert item.results[0].allocated_cost == D("49.50")


# --- Cases 4-5: stored as values, empty = 0, own rate ------------------------------------------


def test_a_later_change_of_the_settings_rate_does_not_change_old_productions(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    send(prepared_client, form([6], (article_id, "5"), labor_minutes="60"))

    with db() as session, session.begin():
        session.get(AppSettings, 1).labour_rate = D("99.00")

    (item,) = productions()
    assert (item.labor_rate, item.labor_cost) == (D("30"), D("30.00"))
    assert "30" in prepared_client.get("/production/1").text


def test_an_empty_time_means_no_labour_and_a_changed_rate_counts_only_here(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()

    send(prepared_client, form([6], (article_id, "5"), labor_minutes="", labor_rate="50"))

    (item,) = productions()
    assert (item.labor_minutes, item.labor_cost, item.total_cost) == (D(0), D(0), D("4.50"))
    with db() as session:
        assert session.get(AppSettings, 1).labour_rate == D("30.00")


def test_numbers_with_comma_are_understood(prepared_client: AppClient) -> None:
    article_id = add_article()

    send(prepared_client, form([6], (article_id, "5"), labor_minutes="7,5", labor_rate="24,8"))

    (item,) = productions()
    assert (item.labor_minutes, item.labor_rate, item.labor_cost) == (
        D("7.5"),
        D("24.8"),
        D("3.10"),
    )


# --- Case 6: bad input -------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["abc", "-1", "1,23456", "1234567", "1e3", "1.2.3"])
@pytest.mark.parametrize("name", ["labor_minutes", "labor_rate"])
def test_bad_labour_input_shows_an_error_at_the_field_and_books_nothing(
    prepared_client: AppClient, name: str, value: str
) -> None:
    article_id = add_article()
    data = form([6], (article_id, "5"), labor_minutes="60", labor_rate="30")
    data[name] = value

    response = send(prepared_client, data)

    assert response.status_code == 422
    assert field_error(response.text, name)
    assert input_value(response.text, name) == value
    assert productions() == []


# --- Case 7: detail page and list --------------------------------------------------------------


def test_the_detail_page_shows_labour_only_when_a_time_was_given(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    send(prepared_client, form([6], (article_id, "5"), labor_minutes="90", labor_rate="30"))
    send(prepared_client, form([7], (article_id, "5")))

    with_labor = prepared_client.get("/production/1").text
    without = prepared_client.get("/production/2").text

    assert DE["production.detail.labor"] in with_labor
    assert "90" in with_labor and "45" in with_labor
    assert DE["production.detail.labor"] not in without
    assert "49,5" in prepared_client.get("/production/list").text


# --- Case 10: texts ----------------------------------------------------------------------------


def test_all_labour_texts_exist_in_both_languages() -> None:
    keys = [
        "production.field.labor_minutes",
        "production.field.labor_rate",
        "production.preview.print",
        "production.preview.labor",
        "production.detail.labor",
        "production.error.labor_number",
    ]
    for key in keys:
        assert DE.get(key) and EN.get(key), key
