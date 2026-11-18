"""Analysis of everything with a period (issue #206, test cases 1-10). All data is invented.

The test case numbers refer to the comment on the issue. Case 11 (desktop and phone view) is a
browser test in tests/browser/test_analysis_total.py.
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import analysis_total, production, sales
from app.main import create_app
from app.models import Sale
from app.production import PrintInput, ResultInput
from tests.auth import READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_stock import add_article, add_movement, db

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal
TODAY = date(2026, 11, 18)
YEAR = (date(2026, 1, 1), date(2026, 12, 31))
_archive = iter(range(1, 10_000))


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def book(
    article_id: int, quantity: int, total: str, day: date, failed_cost: str | None = None
) -> int:
    """Books a production of `quantity` pieces; the good print costs `total`. A failed print
    with the cost `failed_cost` can be added."""
    prints = [PrintInput(next(_archive), D(total), D(0), D(0))]
    if failed_cost is not None:
        prints.append(PrintInput(next(_archive), D(failed_cost), D(0), D(0), failed=True))
    with db() as session, session.begin():
        production_id, errors = production.book(
            session, None, day, "", prints, ResultInput(article_id, quantity)
        )
    assert not errors
    return production_id


def sell(article_id: int, quantity: int, price: str, day: date) -> int:
    with db() as session, session.begin():
        values = {
            "article_id": article_id,
            "quantity": quantity,
            "price": D(price),
            "date": day,
            "note": "",
        }
        assert not sales.book(session, None, values)
        return session.query(Sale).order_by(Sale.id.desc()).first().id


def totals(period=YEAR, today=TODAY) -> analysis_total.Totals:
    with db() as session:
        return analysis_total.compute(session, period[0], period[1], today)


def page(client: AppClient, query: str = "") -> str:
    response = client.get(f"/analysis{query}")
    assert response.status_code == 200
    return response.text


def text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def figure(html: str, key: str) -> str:
    label = re.escape(DE[f"analysis_total.{key}"])
    pattern = rf'<p class="stat-label">\s*{label}\s*</p>\s*<p class="stat-value">(.*?)</p>'
    found = re.search(pattern, html, re.DOTALL)
    assert found, key
    return text(found.group(1))


def table_text(html: str, table_id: str) -> str:
    found = re.search(rf'<article[^>]*id="{table_id}".*?</article>', html, re.DOTALL)
    assert found, table_id
    return text(found.group(0))


def numbers_in(html: str, table_id: str) -> list[str]:
    """The article numbers in the first column of the table, in order."""
    found = re.search(rf'<article[^>]*id="{table_id}".*?</article>', html, re.DOTALL)
    assert found, table_id
    return re.findall(r'<td class="cell-strong"><a[^>]*>(.*?)</a>', found.group(0))


# --- Case 1: revenue in the period -------------------------------------------------------


def test_revenue_is_quantity_times_price_inside_the_period_only() -> None:
    article_id = add_article()
    book(article_id, 50, "50.00", date(2025, 12, 1))
    sell(article_id, 3, "10.00", date(2026, 3, 1))
    sell(article_id, 2, "12.50", date(2026, 6, 1))
    sell(article_id, 5, "9.00", date(2025, 12, 31))  # before the period
    cancelled = sell(article_id, 4, "100.00", date(2026, 7, 1))
    with db() as session, session.begin():
        assert sales.cancel(session, None, cancelled) is None

    assert totals().revenue == D("55.00")  # 30 + 25; the other two do not count


def test_the_first_and_last_day_of_the_period_belong_to_it() -> None:
    article_id = add_article()
    book(article_id, 10, "10.00", date(2026, 1, 1))
    sell(article_id, 1, "7.00", date(2026, 1, 1))
    sell(article_id, 1, "8.00", date(2026, 12, 31))

    assert totals().revenue == D("15.00")


# --- Case 2: moving average --------------------------------------------------------------


def test_cost_of_sold_pieces_uses_the_average_at_the_day_of_each_sale() -> None:
    article_id = add_article()
    book(article_id, 10, "50.00", date(2026, 2, 1))  # 5.00 each
    sell(article_id, 4, "9.00", date(2026, 2, 2))
    book(article_id, 10, "70.00", date(2026, 2, 3))  # 7.00 each
    sell(article_id, 4, "9.00", date(2026, 2, 4))

    # First sale 4 x 5.00 = 20.00; the second at (50 + 70) / 20 = 6.00 each = 24.00
    assert totals().cost_of_sold == D("44.00")
    # An earlier period holds only the first sale: it is not recalculated afterwards
    assert totals((date(2026, 2, 1), date(2026, 2, 2))).cost_of_sold == D("20.00")


def test_a_production_cancelled_later_does_not_count_for_the_average() -> None:
    article_id = add_article()
    book(article_id, 10, "50.00", date(2026, 2, 1))
    cancelled = book(article_id, 10, "700.00", date(2026, 2, 2))
    with db() as session, session.begin():
        assert production.cancel(session, None, cancelled) is None
    sell(article_id, 4, "9.00", date(2026, 2, 3))

    assert totals().cost_of_sold == D("20.00")


def test_a_sale_before_any_production_has_no_cost() -> None:
    article_id = add_article()
    add_movement(article_id, 5)  # a correction as the starting stock
    sell(article_id, 2, "10.00", date(2026, 2, 3))

    result = totals()
    assert result.revenue == D("20.00")
    assert result.cost_of_sold == D(0)


# --- Case 3: gross profit ----------------------------------------------------------------


def test_gross_profit_is_revenue_minus_cost_of_sold_pieces() -> None:
    article_id = add_article()
    book(article_id, 10, "50.00", date(2026, 2, 1))
    sell(article_id, 4, "9.00", date(2026, 2, 2))

    result = totals()
    assert (result.revenue, result.cost_of_sold, result.gross_profit) == (
        D("36.00"),
        D("20.00"),
        D("16.00"),
    )


# --- Case 4: top and flop ----------------------------------------------------------------


def article_with_profit(number: str, profit: str) -> None:
    """An article that sold one piece for `profit` over its cost of 1.00."""
    article_id = add_article(number, f"Artikel {number}")
    book(article_id, 1, "1.00", date(2026, 2, 1))
    sell(article_id, 1, str(D(profit) + 1), date(2026, 2, 2))


def test_top_and_flop_are_ordered_and_limited_to_five() -> None:
    for index in range(1, 13):
        article_with_profit(f"3D-{index:04d}", str(index))
    add_article("3D-9999", "Ohne Verkauf")

    result = totals()

    assert [line.number for line in result.top] == [f"3D-{i:04d}" for i in (12, 11, 10, 9, 8)]
    assert [line.number for line in result.flop] == [f"3D-{i:04d}" for i in (1, 2, 3, 4, 5)]


def test_an_article_is_never_in_top_and_flop_at_once() -> None:
    for index in range(1, 8):  # seven articles: fewer than ten
        article_with_profit(f"3D-{index:04d}", str(index))

    result = totals()

    assert [line.number for line in result.top] == [f"3D-{i:04d}" for i in (7, 6, 5, 4, 3)]
    assert [line.number for line in result.flop] == ["3D-0001", "3D-0002"]
    assert not {line.number for line in result.top} & {line.number for line in result.flop}


def test_top_counts_only_articles_with_sales_in_the_period() -> None:
    article_with_profit("3D-0001", "5")
    other = add_article("3D-0002")
    book(other, 5, "5.00", date(2026, 2, 1))
    sell(other, 1, "50.00", date(2025, 5, 1))  # sold, but outside of the period

    result = totals()

    assert [line.number for line in result.top] == ["3D-0001"]
    assert result.flop == []


# --- Case 5: stock value -----------------------------------------------------------------


def test_stock_value_is_stock_times_average_cost_per_piece() -> None:
    first = add_article("3D-0001")
    book(first, 10, "50.00", date(2026, 2, 1))
    book(first, 10, "70.00", date(2026, 2, 2))  # average 6.00
    sell(first, 5, "9.00", date(2026, 2, 3))  # 15 pieces left: 90.00
    empty = add_article("3D-0002")
    book(empty, 2, "8.00", date(2026, 2, 1))
    sell(empty, 2, "9.00", date(2026, 2, 3))  # no stock: counts 0
    unknown = add_article("3D-0003")
    add_movement(unknown, 4)  # stock without a production: counts 0

    assert totals().stock_value == D("90.00")


def test_stock_value_ignores_the_period() -> None:
    article_id = add_article()
    book(article_id, 10, "50.00", date(2025, 2, 1))

    assert totals().stock_value == D("50.00")
    assert totals((date(2024, 1, 1), date(2024, 12, 31))).stock_value == D("50.00")


# --- Case 6: slow sellers ----------------------------------------------------------------


def test_slow_sellers_have_stock_and_no_sale_for_90_days() -> None:
    never = add_article("3D-0001", "Nie verkauft")
    book(never, 3, "3.00", date(2026, 1, 1))
    old = add_article("3D-0002", "Lange her")
    book(old, 3, "3.00", date(2026, 1, 1))
    sell(old, 1, "5.00", TODAY - timedelta(days=91))
    border = add_article("3D-0003", "Genau 90 Tage")
    book(border, 3, "3.00", date(2026, 1, 1))
    sell(border, 1, "5.00", TODAY - timedelta(days=90))
    recent = add_article("3D-0004", "Vor 10 Tagen")
    book(recent, 3, "3.00", date(2026, 1, 1))
    sell(recent, 1, "5.00", TODAY - timedelta(days=10))
    sold_out = add_article("3D-0005", "Bestand 0")
    book(sold_out, 1, "1.00", date(2026, 1, 1))
    sell(sold_out, 1, "5.00", TODAY - timedelta(days=200))

    result = totals()

    assert [row.number for row in result.slow] == ["3D-0001", "3D-0002"]
    assert result.slow[0].last_sale is None
    assert result.slow[1].last_sale == TODAY - timedelta(days=91)
    assert result.slow[1].stock == 2


def test_a_cancelled_sale_does_not_make_an_article_a_fast_seller() -> None:
    article_id = add_article()
    book(article_id, 3, "3.00", date(2026, 1, 1))
    recent = sell(article_id, 1, "5.00", TODAY - timedelta(days=5))
    with db() as session, session.begin():
        assert sales.cancel(session, None, recent) is None

    assert [row.article_id for row in totals().slow] == [article_id]


def test_slow_sellers_ignore_the_period() -> None:
    article_id = add_article()
    book(article_id, 3, "3.00", date(2026, 1, 1))

    assert len(totals((date(2020, 1, 1), date(2020, 1, 31))).slow) == 1


# --- Case 7: failed prints ---------------------------------------------------------------


def test_failed_print_costs_count_by_production_day_and_not_when_cancelled() -> None:
    article_id = add_article()
    book(article_id, 5, "5.00", date(2026, 3, 1), failed_cost="4.00")
    book(article_id, 5, "5.00", date(2026, 3, 2), failed_cost="1.50")
    book(article_id, 5, "5.00", date(2025, 3, 2), failed_cost="100.00")  # outside
    cancelled = book(article_id, 5, "5.00", date(2026, 3, 3), failed_cost="200.00")
    with db() as session, session.begin():
        assert production.cancel(session, None, cancelled) is None

    assert totals().failed_cost == D("5.50")


# --- Case 8: period selection ------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "start", "end"),
    [
        ("month", date(2026, 11, 1), date(2026, 11, 30)),
        ("last_month", date(2026, 10, 1), date(2026, 10, 31)),
        ("year", date(2026, 1, 1), date(2026, 12, 31)),
        ("last_year", date(2025, 1, 1), date(2025, 12, 31)),
    ],
)
def test_the_named_periods(key: str, start: date, end: date) -> None:
    chosen = analysis_total.choose(key, "", "", TODAY)

    assert (chosen.key, chosen.start, chosen.end, chosen.error) == (key, start, end, None)


def test_last_month_in_january_is_december_of_the_year_before() -> None:
    chosen = analysis_total.choose("last_month", "", "", date(2027, 1, 15))

    assert (chosen.start, chosen.end) == (date(2026, 12, 1), date(2026, 12, 31))


def test_without_a_choice_or_with_an_unknown_one_it_is_this_year() -> None:
    for key in (None, "", "forever"):
        chosen = analysis_total.choose(key, "", "", TODAY)
        assert (chosen.key, chosen.start, chosen.end) == ("year", *YEAR)


def test_a_free_period_uses_the_two_dates() -> None:
    chosen = analysis_total.choose("custom", "2026-02-10", "2026-03-05", TODAY)

    assert (chosen.start, chosen.end, chosen.error) == (date(2026, 2, 10), date(2026, 3, 5), None)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-03-05", "2026-02-10"),  # end before start
        ("", "2026-02-10"),  # no date
        ("2026-02-10", ""),
        ("gestern", "2026-02-10"),  # not a date
        ("2026-02-30", "2026-03-10"),  # not a day
        ("2026-W10-1", "2026-W11-1"),  # week dates are not accepted
        ("2026-01-01" + "0" * 500, "2026-03-10"),  # far too long
        ("2026-01-01", "2030-01-01"),  # too far in the future
    ],
)
def test_invalid_free_periods_are_an_error(start: str, end: str) -> None:
    chosen = analysis_total.choose("custom", start, end, TODAY)

    assert chosen.error == "analysis_total.error.period"


def test_a_very_long_period_text_gives_the_error_and_not_a_crash(client: AppClient) -> None:
    html = page(client, f"?period={'x' * 500}&from={'1' * 500}&to=2026-01-01")

    assert re.search(r'<option value="year"\s+selected', html)


def test_the_page_shows_an_error_and_no_figures_for_an_invalid_period(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 5, "5.00", date(2026, 3, 1))
    sell(article_id, 1, "9.00", date(2026, 3, 2))

    html = page(client, "?period=custom&from=2026-05-01&to=2026-04-01")

    assert DE["analysis_total.error.period"] in html
    assert 'class="stat-value"' not in html


def test_the_page_uses_the_chosen_period(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 50, "50.00", date(2026, 1, 5))
    sell(article_id, 2, "10.00", date(2026, 1, 10))
    sell(article_id, 3, "10.00", date(2026, 2, 10))

    january = page(client, "?period=custom&from=2026-01-01&to=2026-01-31")
    february = page(client, "?period=custom&from=2026-02-01&to=2026-02-28")

    assert figure(january, "revenue") == "20,00 €"
    assert figure(february, "revenue") == "30,00 €"
    # Stock value and slow sellers are of today and do not change with the period
    assert figure(january, "stock_value") == figure(february, "stock_value")


def test_the_default_period_is_this_year_and_is_shown_as_chosen(client: AppClient) -> None:
    html = page(client)

    assert re.search(r'<option value="year"\s+selected', html)


# --- Case 9: no data ---------------------------------------------------------------------


def test_without_data_everything_is_zero_and_the_tables_say_so(client: AppClient) -> None:
    html = page(client, "?period=custom&from=2026-01-01&to=2026-12-31")

    for key in ("revenue", "cost_of_sold", "gross_profit", "stock_value", "failed_cost"):
        assert figure(html, key) == "0,00 €"
    for table in ("top", "flop", "slow"):
        assert DE[f"analysis_total.empty.{table}"] in html


# --- Case 10: access and navigation ------------------------------------------------------


def test_the_menu_entry_is_a_link_for_everyone_and_current_on_the_page(
    client: AppClient,
) -> None:
    bar = re.search(r"<header.*?</header>", page(client), re.DOTALL).group(0)

    assert re.search(r'<a[^>]*href="/analysis"[^>]*aria-current="page"', bar)
    assert not re.findall(r'<a\b[^>]*aria-disabled="true"', bar)


def test_a_reader_may_look(client: AppClient) -> None:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        html = page(reader)
        assert 'href="/analysis"' in html


def test_without_login_the_page_is_not_reachable() -> None:
    with AppClient(create_app()) as anonymous:
        response = anonymous.get("/analysis", follow_redirects=False)

    assert response.status_code in (303, 401)
    assert "stat-value" not in response.text


def test_the_page_lists_top_flop_and_slow_sellers(client: AppClient) -> None:
    for index in range(1, 4):
        article_with_profit(f"3D-{index:04d}", str(index))
    slow = add_article("3D-0099", "Ladenhüter")
    book(slow, 2, "2.00", date(2026, 1, 1))

    html = page(client, "?period=custom&from=2026-01-01&to=2026-12-31")

    assert numbers_in(html, "top") == ["3D-0003", "3D-0002", "3D-0001"]
    assert numbers_in(html, "flop") == []
    assert "3D-0099" in numbers_in(html, "slow")
    assert DE["analysis_total.empty.flop"] in html


def test_the_german_and_english_texts_have_the_same_keys() -> None:
    assert {k for k in DE if k.startswith("analysis_total.")} == {
        k for k in EN if k.startswith("analysis_total.")
    }
