"""Analysis per article (issue #203, test cases 1-13). All data is invented.

The test case numbers refer to the comment on the issue. Case 14 (desktop and phone view) is a
browser test in tests/browser/test_article_analysis.py.
"""

import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app import production, sales, stock
from app.main import create_app
from app.models import AppSettings, Calculation, Sale
from app.production import LaborInput, PrintInput, ResultInput
from tests.auth import READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_stock import add_article, db

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal
DASH = "\u2013"
_archive = iter(range(1, 10_000))


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def book(
    article_id: int,
    quantity: int,
    total: str,
    failed_pieces: int = 0,
    labor: LaborInput | None = None,
) -> int:
    """Books a production; the prints cost `total` minus the labour cost together."""
    printed = D(total) - (labor.cost if labor else D(0))
    with db() as session, session.begin():
        production_id, errors = production.book(
            session,
            None,
            date(2026, 11, 15),
            "",
            [PrintInput(next(_archive), printed, D(0), D(0))],
            ResultInput(article_id, quantity),
            labor=labor,
            failed_pieces=failed_pieces,
        )
    assert not errors
    return production_id


def cancel_production(production_id: int) -> None:
    with db() as session, session.begin():
        assert production.cancel(session, None, production_id) is None


def sell(article_id: int, quantity: int, price: str) -> int:
    with db() as session, session.begin():
        values = {
            "article_id": article_id,
            "quantity": quantity,
            "price": D(price),
            "date": date(2026, 11, 16),
            "note": "",
        }
        assert not sales.book(session, None, values)
        return session.query(Sale).order_by(Sale.id.desc()).first().id


def cancel_sale(sale_id: int) -> None:
    with db() as session, session.begin():
        assert sales.cancel(session, None, sale_id) is None


def calculate(article_id: int, cost_with_labour: str, main: bool = True) -> None:
    with db() as session, session.begin():
        session.add(
            Calculation(
                article_id=article_id,
                created_on=date(2026, 11, 1),
                created_at=datetime(2026, 11, 1),
                kind="hand",
                is_main=main,
                printer_name="Testdrucker",
                print_minutes=D(60),
                pieces=1,
                setup_minutes=D(0),
                rework_minutes=D(0),
                scrap_percent=D(0),
                margin_percent=D(0),
                power_price=D("0.3"),
                machine_rate=D(0),
                labour_rate=D(0),
                rounding="cent",
                material_per_plate=D(0),
                power_per_plate=D(0),
                machine_per_plate=D(0),
                labour_cost=D(0),
                cost_without_labour=D(cost_with_labour),
                cost_with_labour=D(cost_with_labour),
                suggested_price=D(0),
                selling_price=D(0),
                profit=D(0),
            )
        )


def set_threshold(value: str) -> None:
    with db() as session, session.begin():
        session.get(AppSettings, 1).deviation_threshold = D(value)


def page(client: AppClient, article_id: int) -> str:
    response = client.get(f"/articles/{article_id}/analysis")
    assert response.status_code == 200
    return response.text


def text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def figure(html: str, label_key: str) -> str:
    """The value of the key figure tile that has the label."""
    label = re.escape(DE[label_key])
    pattern = rf'<p class="stat-label">\s*{label}\s*</p>\s*<p class="stat-value">(.*?)</p>'
    found = re.search(pattern, html, re.DOTALL)
    assert found, label_key
    return text(found.group(1))


# Case 1
def test_cost_per_piece_is_total_cost_over_total_good_pieces(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 10, "50.00")
    book(article_id, 10, "70.00")
    assert figure(page(client, article_id), "analysis.cost_actual") == "6,00 €"


# Case 2
def test_a_cancelled_production_does_not_count(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 10, "50.00", failed_pieces=1)
    cancelled = book(article_id, 10, "300.00", failed_pieces=5)
    cancel_production(cancelled)
    html = page(client, article_id)
    assert figure(html, "analysis.cost_actual") == "5,00 €"
    assert figure(html, "analysis.produced") == "10"
    assert figure(html, "analysis.failed_share") == "9,1 %"


# Case 3
def test_average_price_is_weighted_and_skips_cancelled_sales(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 30, "100.00")
    sell(article_id, 4, "12.00")
    sell(article_id, 6, "10.00")
    cancel_sale(sell(article_id, 5, "99.00"))
    html = page(client, article_id)
    assert figure(html, "analysis.price_average") == "10,80 €"
    assert figure(html, "analysis.sold") == "10"


# Case 4
def test_profit_per_piece_in_euro_and_percent(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 20, "120.00")
    sell(article_id, 4, "12.00")
    sell(article_id, 6, "10.00")
    html = page(client, article_id)
    assert figure(html, "analysis.profit") == "4,80 €"
    assert figure(html, "analysis.profit_percent") == "44,4 %"


# Case 5
def test_effective_hourly_wage_takes_the_labour_cost_out(client: AppClient) -> None:
    article_id = add_article()
    # 10 pieces, 60 minutes at 20 Euro per hour = 20 Euro labour, 30 Euro for the rest
    book(article_id, 10, "50.00", labor=LaborInput(D(60), D(20)))
    sell(article_id, 10, "10.80")
    assert figure(page(client, article_id), "analysis.hourly_wage") == "78,00 €"


def test_without_labour_time_the_hourly_wage_is_a_dash(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 10, "50.00")
    sell(article_id, 10, "10.80")
    assert figure(page(client, article_id), "analysis.hourly_wage") == DASH


# Case 6
def test_produced_sold_and_stock_match_the_bookings(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 12, "60.00")
    book(article_id, 8, "40.00")
    sell(article_id, 5, "9.00")
    with db() as session:
        assert stock.stock_of(session, article_id) == 15
    html = page(client, article_id)
    assert figure(html, "analysis.produced") == "20"
    assert figure(html, "analysis.sold") == "5"
    assert figure(html, "analysis.stock") == "15"


# Case 7
def test_failed_share(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 9, "45.00", failed_pieces=1)
    assert figure(page(client, article_id), "analysis.failed_share") == "10 %"


# Case 8
def test_planned_cost_comes_from_the_main_calculation(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 10, "50.00")
    html = page(client, article_id)
    assert figure(html, "analysis.cost_planned") == DASH
    assert figure(html, "analysis.deviation") == DASH
    assert DE["analysis.warning"] not in html
    calculate(article_id, "9.99", main=False)
    calculate(article_id, "4.00")
    html = page(client, article_id)
    assert figure(html, "analysis.cost_planned") == "4,00 €"
    assert figure(html, "analysis.deviation") == "25 %"


# Case 9
def test_warning_depends_on_the_threshold_from_the_settings(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 10, "60.00")
    calculate(article_id, "5.00")  # 6.00 against 5.00 = 20 %
    assert DE["analysis.warning"] in page(client, article_id)
    set_threshold("25")
    assert DE["analysis.warning"] not in page(client, article_id)
    set_threshold("15")
    other = add_article("3D-0002")
    book(other, 10, "55.00")
    calculate(other, "5.00")  # 10 %
    assert DE["analysis.warning"] not in page(client, other)


def test_cheaper_than_planned_by_more_than_the_threshold_also_warns(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 10, "30.00")
    calculate(article_id, "5.00")  # 3.00 against 5.00 = -40 %
    html = page(client, article_id)
    assert figure(html, "analysis.deviation") == "-40 %"
    assert DE["analysis.warning"] in html
    set_threshold("50")
    assert DE["analysis.warning"] not in page(client, article_id)


def test_failed_pieces_are_counted(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 9, "45.00", failed_pieces=1)
    book(article_id, 5, "25.00", failed_pieces=2)
    assert figure(page(client, article_id), "analysis.failed") == "3"


def test_the_tile_has_no_count_on_the_article(client: AppClient) -> None:
    article_id = add_article()
    detail = client.get(f"/articles/{article_id}").text
    assert DE["article.tile.analysis_open"] in detail


def test_money_is_shown_with_two_decimals(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, 3, "100.00")
    assert figure(page(client, article_id), "analysis.cost_actual") == "33,33 €"


# Case 10
def test_an_article_without_anything_shows_dashes_and_no_error(client: AppClient) -> None:
    article_id = add_article()
    html = page(client, article_id)
    for key in (
        "analysis.cost_actual",
        "analysis.cost_planned",
        "analysis.deviation",
        "analysis.price_average",
        "analysis.profit",
        "analysis.profit_percent",
        "analysis.hourly_wage",
        "analysis.failed_share",
    ):
        assert figure(html, key) == DASH
    for key in ("analysis.produced", "analysis.sold", "analysis.stock", "analysis.failed"):
        assert figure(html, key) == "0"
    assert DE["analysis.warning"] not in html


def test_production_without_sale_and_sale_price_of_zero_do_not_divide_by_zero(
    client: AppClient,
) -> None:
    article_id = add_article()
    book(article_id, 10, "50.00")
    html = page(client, article_id)
    assert figure(html, "analysis.cost_actual") == "5,00 €"
    assert figure(html, "analysis.profit") == DASH
    sell(article_id, 2, "0")
    html = page(client, article_id)
    assert figure(html, "analysis.profit") == "-5,00 €"
    assert figure(html, "analysis.profit_percent") == DASH


# Case 11
def test_a_variant_is_analysed_on_its_own(client: AppClient) -> None:
    template_like = add_article("3D-0001")
    variant = add_article("3D-0001-A", name="Kabelhalter rot")
    book(template_like, 10, "100.00")
    book(variant, 10, "30.00")
    assert figure(page(client, variant), "analysis.cost_actual") == "3,00 €"
    assert figure(page(client, template_like), "analysis.cost_actual") == "10,00 €"


# Case 12
def test_every_role_sees_the_page_and_others_do_not(client: AppClient) -> None:
    article_id = add_article()
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        assert reader.get(f"/articles/{article_id}/analysis").status_code == 200
    with AppClient(create_app()) as anonymous:
        response = anonymous.get(f"/articles/{article_id}/analysis", follow_redirects=False)
        assert response.status_code in (302, 303, 401)
        assert "/login" in response.headers.get("location", "/login")
    assert client.get("/articles/9999/analysis").status_code == 404


# Case 13
def test_the_tile_leads_to_the_page_with_the_path(client: AppClient) -> None:
    article_id = add_article()
    detail = client.get(f"/articles/{article_id}").text
    assert f'href="/articles/{article_id}/analysis"' in detail
    html = page(client, article_id)
    assert f'<a href="/articles/{article_id}">3D-0001</a>' in html
    assert f'<a href="/articles">{DE["articles.title"]}</a>' in html
    assert f"<li>{DE['article.tile.analysis']}</li>" in html


def test_all_texts_exist_in_both_languages() -> None:
    keys = [key for key in DE if key.startswith("analysis.")]
    assert keys
    assert set(keys) == {key for key in EN if key.startswith("analysis.")}
