"""Chart "cost per piece over time" on the analysis page (issue #204, test cases 1-6 and 8).

The test case numbers refer to the comment on the issue. Case 7 (dark and light theme, phone
view) is a browser test in tests/browser/test_article_analysis_chart.py. All data is invented.
"""

import html as html_lib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import production
from app.production import PrintInput, ResultInput
from tests.auth import AppClient, signed_in_client
from tests.test_article_analysis import DE, EN, calculate, cancel_production, text
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_stock import add_article, db

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"
D = Decimal
_archive = iter(range(50_000, 60_000))


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def book_on(article_id: int, day: date, quantity: int, total: str) -> int:
    with db() as session, session.begin():
        production_id, errors = production.book(
            session,
            None,
            day,
            "",
            [PrintInput(next(_archive), D(total), D(0), D(0))],
            ResultInput(article_id, quantity),
        )
    assert not errors
    return production_id


def page(client: AppClient, article_id: int) -> str:
    response = client.get(f"/articles/{article_id}/analysis")
    assert response.status_code == 200
    return response.text


def chart_data(html: str) -> dict:
    """The data the page hands to the chart, read the way the browser reads the attribute."""
    found = re.search(r'<canvas\b[^>]*\bdata-chart="([^"]*)"', html)
    assert found, "no chart on the page"
    return json.loads(html_lib.unescape(found.group(1)))


# Case 1
def test_three_productions_give_three_points_in_date_order(client: AppClient) -> None:
    article_id = add_article()
    # Booked out of order on purpose: the points follow the date
    book_on(article_id, date(2026, 11, 12), 4, "30.00")
    book_on(article_id, date(2026, 11, 10), 10, "50.00")
    book_on(article_id, date(2026, 11, 11), 3, "10.00")
    points = chart_data(page(client, article_id))["points"]
    assert [(p["date"], p["quantity"], p["cost"]) for p in points] == [
        ("2026-11-10", 10, "5.00"),
        ("2026-11-11", 3, "3.33"),
        ("2026-11-12", 4, "7.50"),
    ]


# Case 2
def test_a_cancelled_production_has_no_point(client: AppClient) -> None:
    article_id = add_article()
    book_on(article_id, date(2026, 11, 10), 10, "50.00")
    cancelled = book_on(article_id, date(2026, 11, 11), 10, "90.00")
    cancel_production(cancelled)
    html = page(client, article_id)
    assert [p["cost"] for p in chart_data(html)["points"]] == ["5.00"]
    assert "9,00" not in text(html)


# Case 3
def test_the_main_calculation_gives_a_planned_line_and_without_it_there_is_none(
    client: AppClient,
) -> None:
    article_id = add_article()
    book_on(article_id, date(2026, 11, 10), 10, "50.00")
    assert chart_data(page(client, article_id))["planned"] is None
    calculate(article_id, "4.50", main=False)
    assert chart_data(page(client, article_id))["planned"] is None
    calculate(article_id, "4.50")
    assert chart_data(page(client, article_id))["planned"] == "4.50"


# Case 4
def test_without_production_there_is_a_hint_and_no_chart(client: AppClient) -> None:
    article_id = add_article()
    html = page(client, article_id)
    assert DE["analysis.chart.empty"] in html
    assert "<canvas" not in html
    assert "analysis_chart.js" not in html
    # Only cancelled productions: same as none
    cancel_production(book_on(article_id, date(2026, 11, 10), 5, "10.00"))
    html = page(client, article_id)
    assert DE["analysis.chart.empty"] in html
    assert "<canvas" not in html


# Case 5
def test_the_table_below_the_chart_shows_the_same_values(client: AppClient) -> None:
    article_id = add_article()
    book_on(article_id, date(2026, 11, 10), 10, "50.00")
    book_on(article_id, date(2026, 11, 11), 3, "10.00")
    html = page(client, article_id)
    rows = re.findall(r"<tr>\s*<td[^>]*>(.*?)</tr>", html.split("</canvas>", 1)[1], re.DOTALL)
    assert [text(row) for row in rows] == [
        "10.11.2026 10 5,00 €",
        "11.11.2026 3 3,33 €",
    ]
    for key in ("analysis.chart.date", "analysis.chart.pieces", "analysis.chart.cost"):
        assert DE[key] in html


# Case 6
def test_chart_js_is_served_by_the_app_itself(client: AppClient) -> None:
    article_id = add_article()
    book_on(article_id, date(2026, 11, 10), 10, "50.00")
    html = page(client, article_id)
    scripts = re.findall(r'<script\b[^>]*src="([^"]+)"', html)
    assert "/static/chart.min.js" in scripts
    assert "/static/analysis_chart.js" in scripts
    assert all(src.startswith("/") and not src.startswith("//") for src in scripts)
    served = client.get("/static/chart.min.js")
    assert served.status_code == 200
    assert "Chart.js v" in served.text[:200]
    assert (STATIC_DIR / "chart.LICENSE.md").is_file()
    assert "MIT" in (STATIC_DIR / "chart.LICENSE.md").read_text(encoding="utf-8")
    # No inline script and no foreign address anywhere on the page
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r'(?:src|href)="(?:https?:)?//', html)


def test_chart_js_is_loaded_only_on_the_analysis_page(client: AppClient) -> None:
    article_id = add_article()
    assert "chart.min.js" not in client.get(f"/articles/{article_id}").text
    assert "chart.min.js" not in client.get("/articles").text


# Case 8
def test_the_data_is_embedded_safely(client: AppClient) -> None:
    article_id = add_article()
    book_on(article_id, date(2026, 11, 10), 10, "50.00")
    html = page(client, article_id)
    attribute = re.search(r'\bdata-chart="([^"]*)"', html).group(1)
    # Nothing in the attribute can end it or open a tag
    assert "<" not in attribute and ">" not in attribute
    # The labels come from the translation files, never from article data
    data = chart_data(html)
    assert data["labels"]["actual"] == DE["analysis.chart.actual"]
    assert data["labels"]["planned"] == DE["analysis.chart.planned"]
    assert data["labels"]["pieces"] == DE["analysis.chart.pieces"]
    assert set(data) == {"points", "planned", "labels", "language"}


def test_chart_texts_exist_in_both_languages() -> None:
    keys = [k for k in DE if k.startswith("analysis.chart.")]
    assert len(keys) >= 6
    assert {k for k in EN if k.startswith("analysis.chart.")} == set(keys)


# Limit of the table (answer to the maintainer's question on the pull request)
def test_the_table_shows_only_the_last_20_productions_but_the_chart_all(
    client: AppClient,
) -> None:
    article_id = add_article()
    for day in range(1, 26):
        book_on(article_id, date(2026, 10, day), 1, f"{day}.00")
    html = page(client, article_id)
    assert len(chart_data(html)["points"]) == 25
    rows = re.findall(r"<tr><td>(.*?)</td>", html)
    assert len(rows) == 20
    # The newest 20, oldest of them first: 6 to 25 October
    assert rows[0] == "06.10.2026" and rows[-1] == "25.10.2026"
    assert DE["analysis.chart.limited"].format(shown=20, total=25) in text(html)


def test_up_to_20_productions_show_no_limit_hint(client: AppClient) -> None:
    article_id = add_article()
    for day in range(1, 21):
        book_on(article_id, date(2026, 10, day), 1, f"{day}.00")
    html = page(client, article_id)
    assert len(re.findall(r"<tr><td>", html)) == 20
    assert DE["analysis.chart.limited"].split("{")[0] not in text(html)
