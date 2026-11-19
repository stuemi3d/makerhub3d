"""Dashboard with all areas and the monthly chart (issue #207, test cases 1-8). All data is
invented. The test case numbers refer to the comment on the issue; case 8 (desktop and phone
view) is a browser test in tests/browser/test_dashboard.py."""

import json
import re
from datetime import date
from decimal import Decimal

import pytest

from app import analysis_total, dashboard_figures, production
from app.analysis import NONE_TEXT
from app.production import PrintInput, ResultInput
from tests.auth import READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.test_analysis_total import DE, EN, TODAY, YEAR, sell
from tests.test_article_analysis import calculate, set_threshold
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_stock import add_article, db

D = Decimal
PERIOD = "?period=custom&from=2026-01-01&to=2026-12-31"
_archive = iter(range(50_000, 60_000))


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def make(
    article_id: int,
    quantity: int,
    cost: str,
    day: date,
    *,
    failed_pieces: int = 0,
    seconds: int | None = None,
    grams: str | None = None,
    kwh: str | None = None,
    failed_print_cost: str | None = None,
) -> int:
    prints = [
        PrintInput(
            next(_archive),
            D(cost),
            D(0),
            D(0),
            seconds=seconds,
            grams=None if grams is None else D(grams),
            kwh=None if kwh is None else D(kwh),
        )
    ]
    if failed_print_cost is not None:
        prints.append(PrintInput(next(_archive), D(failed_print_cost), D(0), D(0), failed=True))
    with db() as session, session.begin():
        production_id, errors = production.book(
            session,
            None,
            day,
            "",
            prints,
            ResultInput(article_id, quantity),
            failed_pieces=failed_pieces,
        )
    assert not errors
    return production_id


def figures(period=YEAR) -> dashboard_figures.ProductionFigures:
    with db() as session:
        return dashboard_figures.production(session, period[0], period[1])


def tile(html: str, key: str) -> str:
    match = re.search(
        rf'<p class="stat-label">\s*{re.escape(DE[key])}\s*</p>\s*<p class="stat-value">(.*?)</p>',
        html,
        re.DOTALL,
    )
    assert match, key
    return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split())


def page(client: AppClient, query: str = PERIOD) -> str:
    response = client.get(f"/{query}")
    assert response.status_code == 200
    return response.text


# --- Case 1: same numbers as the analysis of everything ----------------------------------


def test_sales_quality_and_stock_tiles_equal_the_analysis_of_everything(
    client: AppClient,
) -> None:
    article = add_article("A-1")
    make(article, 10, "20.00", date(2026, 3, 1), failed_print_cost="6.00")
    sell(article, 4, "5.00", date(2026, 3, 5))

    dash = page(client)
    total = client.get("/analysis" + PERIOD).text

    def analysis(key: str) -> str:
        label = re.escape(DE[f"analysis_total.{key}"])
        pattern = rf'<p class="stat-label">\s*{label}\s*</p>\s*<p class="stat-value">(.*?)</p>'
        return " ".join(re.sub(r"<[^>]+>", " ", re.search(pattern, total, re.S).group(1)).split())

    assert tile(dash, "dashboard.tile.revenue") == analysis("revenue") == "20,00 €"
    assert tile(dash, "dashboard.tile.profit") == analysis("gross_profit")
    assert tile(dash, "dashboard.tile.failed_cost") == analysis("failed_cost") == "6,00 €"
    assert tile(dash, "dashboard.tile.stock_value") == analysis("stock_value")


def test_average_margin_is_profit_divided_by_revenue(client: AppClient) -> None:
    article = add_article("A-1")
    make(article, 10, "20.00", date(2026, 3, 1))
    sell(article, 4, "5.00", date(2026, 3, 5))  # revenue 20, cost 8, profit 12

    assert tile(page(client), "dashboard.tile.margin") == "60 %"


def test_margin_without_revenue_is_a_dash(client: AppClient) -> None:
    assert tile(page(client), "dashboard.tile.margin") == NONE_TEXT


# --- Cases 2 and 3: production and quality ------------------------------------------------


def test_production_figures_sum_good_pieces_cost_hours_grams_and_kwh() -> None:
    first, second = add_article("A-1"), add_article("A-2")
    make(first, 10, "20.00", date(2026, 3, 1), seconds=5400, grams="250", kwh="1.5")
    make(second, 5, "10.00", date(2026, 4, 1), seconds=1800, grams="100.5", kwh="0.5")

    found = figures()

    assert found.pieces == 15
    assert found.cost == D("30.00")
    assert found.hours == D("2")
    assert found.kilograms == D("0.3505")
    assert found.kwh == D("2.0")


def test_unknown_values_count_as_zero_and_never_fail() -> None:
    make(add_article("A-1"), 3, "9.00", date(2026, 3, 1))

    found = figures()

    assert (found.hours, found.kilograms, found.kwh) == (D(0), D(0), D(0))
    assert found.pieces == 3


def test_cancelled_productions_and_other_periods_do_not_count() -> None:
    article = add_article("A-1")
    cancelled = make(article, 10, "20.00", date(2026, 3, 1), seconds=3600)
    make(article, 7, "7.00", date(2025, 12, 31), seconds=3600)
    make(article, 2, "4.00", date(2026, 12, 31), seconds=3600)
    with db() as session, session.begin():
        assert production.cancel(session, None, cancelled) is None

    found = figures()

    assert (found.pieces, found.cost, found.hours) == (2, D("4.00"), D(1))


def test_failed_rate_is_failed_pieces_divided_by_good_and_failed() -> None:
    article = add_article("A-1")
    make(article, 6, "6.00", date(2026, 3, 1), failed_pieces=2)
    make(article, 2, "2.00", date(2026, 3, 2), failed_pieces=0)

    assert figures().failed_rate == D(2) * 100 / D(10)


def test_failed_rate_without_any_piece_is_none() -> None:
    assert figures().failed_rate is None


def test_production_and_quality_tiles_are_shown(client: AppClient) -> None:
    article = add_article("A-1")
    make(article, 6, "6.00", date(2026, 3, 1), failed_pieces=2, seconds=7200, grams="500", kwh="2")

    html = page(client)

    assert tile(html, "dashboard.tile.pieces") == "6"
    assert tile(html, "dashboard.tile.cost") == "6,00 €"
    assert tile(html, "dashboard.tile.hours") == "2"
    assert tile(html, "dashboard.tile.kilograms") == "0,5"
    assert tile(html, "dashboard.tile.kwh") == "2"
    assert tile(html, "dashboard.tile.failed_rate") == "25 %"


# --- Case 4: to do ------------------------------------------------------------------------


def todo(html: str) -> str:
    found = re.search(r'<section id="todo".*?</section>', html, re.DOTALL)
    assert found
    return " ".join(re.sub(r"<[^>]+>", " ", found.group(0)).split())


def test_nothing_to_do_is_said_calmly(client: AppClient) -> None:
    html = todo(page(client))

    assert DE["dashboard.todo.nothing"] in html


def test_warnings_and_slow_sellers_are_counted_with_links(client: AppClient) -> None:
    set_threshold("15")
    article = add_article("A-1")
    calculate(article, "1.00")
    make(article, 1, "2.00", date(2026, 3, 1))  # cost 2.00 against 1.00 planned: +100 %
    quiet = add_article("A-2")
    calculate(quiet, "2.00")
    make(quiet, 1, "2.00", date(2026, 3, 1))

    html = page(client)
    section = re.search(r'<section id="todo".*?</section>', html, re.DOTALL).group(0)

    assert f'href="/articles/{article}/analysis"' in section
    assert f'href="/articles/{quiet}/analysis"' not in section
    assert 'href="/analysis#slow"' in section


def test_warning_count_uses_the_threshold_of_the_settings() -> None:
    article = add_article("A-1")
    calculate(article, "1.00")
    make(article, 1, "1.10", date(2026, 3, 1))  # +10 %
    set_threshold("15")
    with db() as session:
        assert dashboard_figures.warning_articles(session) == []
    set_threshold("5")
    with db() as session:
        assert [a.id for a in dashboard_figures.warning_articles(session)] == [article]


def test_slow_sellers_are_counted_like_in_the_analysis(client: AppClient) -> None:
    article = add_article("A-1")
    make(article, 2, "2.00", date(2026, 3, 1))

    with db() as session:
        slow = analysis_total.compute(session, *YEAR, TODAY).slow
    assert [row.article_id for row in slow] == [article]


# --- Case 5: monthly chart -----------------------------------------------------------------


def months(today: date = TODAY):
    with db() as session:
        return analysis_total.monthly(session, today)


def test_the_chart_has_twelve_months_ending_with_this_one() -> None:
    found = months()

    assert len(found) == 12
    assert found[0].month == date(2025, 12, 1)
    assert found[-1].month == date(2026, 11, 1)


def test_months_without_data_are_zero_and_the_sums_are_per_month() -> None:
    article = add_article("A-1")
    make(article, 10, "20.00", date(2026, 3, 1))
    sell(article, 2, "5.00", date(2026, 3, 5))
    sell(article, 1, "6.00", date(2026, 3, 20))
    sell(article, 1, "7.00", date(2026, 5, 2))
    sell(article, 1, "9.00", date(2025, 11, 30))  # before the 12 months

    by_month = {point.month: point for point in months()}

    assert by_month[date(2026, 3, 1)].revenue == D("16.00")
    assert by_month[date(2026, 3, 1)].cost == D("6.00")
    assert by_month[date(2026, 5, 1)].revenue == D("7.00")
    assert by_month[date(2026, 4, 1)].revenue == 0
    assert by_month[date(2026, 4, 1)].cost == 0
    assert sum(point.revenue for point in by_month.values()) == D("23.00")


def test_a_cancelled_sale_does_not_count_in_the_chart() -> None:
    article = add_article("A-1")
    make(article, 10, "20.00", date(2026, 3, 1))
    sale = sell(article, 2, "5.00", date(2026, 3, 5))
    from app import sales

    with db() as session, session.begin():
        assert sales.cancel(session, None, sale) is None

    assert all(point.revenue == 0 for point in months())


def test_the_page_hands_the_chart_data_as_json(client: AppClient) -> None:
    article = add_article("A-1")
    make(article, 10, "20.00", date(date.today().year, date.today().month, 1))
    sell(article, 2, "5.00", date.today())

    html = client.get("/").text
    raw = re.search(r'<canvas[^>]*data-chart="([^"]*)"', html).group(1)
    data = json.loads(raw.replace("&#34;", '"').replace("&quot;", '"'))

    assert len(data["months"]) == 12
    assert data["months"][-1]["revenue"] == "10.00"
    assert data["months"][-1]["cost"] == "4.00"
    assert '<script src="/static/chart.min.js"' in html


# --- Case 6: Bambuddy away ------------------------------------------------------------------


def test_without_bambuddy_the_dashboard_works_and_the_open_prints_have_a_hint(
    client: AppClient,
) -> None:
    html = page(client)

    assert DE["dashboard.open.not_set_up"] in html
    assert tile(html, "dashboard.tile.revenue") == "0,00 €"


# --- Case 7: access -------------------------------------------------------------------------


def test_a_reader_may_look(client: AppClient) -> None:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        assert reader.get("/").status_code == 200


def test_without_login_the_dashboard_is_not_reachable() -> None:
    from app.main import create_app
    from tests.auth import AppClient as Client

    with Client(create_app(), follow_redirects=False) as anonymous:
        assert anonymous.get("/").status_code in (302, 303, 307)


# --- Period choice ---------------------------------------------------------------------------


def test_the_default_period_is_this_year(client: AppClient) -> None:
    assert re.search(r'<option value="year"\s+selected', client.get("/").text)


def test_an_invalid_period_shows_the_error_and_no_figures(client: AppClient) -> None:
    html = client.get("/?period=custom&from=x&to=y").text

    assert DE["analysis_total.error.period"] in html
    assert DE["dashboard.tile.profit"] not in html


def test_an_invalid_period_keeps_the_to_do_list_and_the_chart(client: AppClient) -> None:
    set_threshold("15")
    article = add_article("A-1")
    calculate(article, "1.00")
    make(article, 1, "2.00", date(2026, 3, 1))

    html = client.get("/?period=custom&from=x&to=y").text

    assert f'href="/articles/{article}/analysis"' in html
    assert DE["dashboard.todo.nothing"] not in html
    assert "data-chart=" in html


def test_sorting_the_low_stock_list_keeps_the_period() -> None:
    from app import dashboard

    links = dashboard.sort_links(None, {"period": "month"})

    assert "period=month" in links["number"]["href"]


def test_the_german_and_english_texts_have_the_same_keys() -> None:
    assert set(DE) == set(EN)
