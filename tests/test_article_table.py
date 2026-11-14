"""Table building block and article list (issue #75, test cases 1-12). All data is invented.

The test case numbers refer to the comment on the issue.
"""

import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.models import Article, ArticleTemplate, StockMovement
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


def add_article(number: str, name: str = "Kabelhalter", **values: object) -> int:
    with db() as session, session.begin():
        article = Article(
            number=number,
            number_key=number.casefold(),
            name=name,
            active=values.pop("active", True),
            created_at=datetime(2026, 1, 1),
            **values,
        )
        session.add(article)
        session.flush()
        return article.id


def add_stock(article_id: int, quantity: int) -> None:
    with db() as session, session.begin():
        session.add(
            StockMovement(
                article_id=article_id,
                booked_on=date(2026, 1, 2),
                created_at=datetime(2026, 1, 2, 10, 0),
                quantity=quantity,
                kind="correction",
                reason="Startbestand",
                user_id=None,
            )
        )


def numbers(client: AppClient, query: str = "") -> list[str]:
    html = client.get("/articles" + query).text
    return re.findall(r'<td class="cell-strong"><a href="/articles/\d+">(.*?)</a>', html)


def row_of(html: str, number: str) -> str:
    rows = re.findall(r"<tr>.*?</tr>", html, re.DOTALL)
    (row,) = [r for r in rows if f">{number}</a>" in r]
    return row


@pytest.fixture
def employee(client: AppClient) -> AppClient:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as test_client:
        yield test_client


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


# --- Columns, chip, numbers (cases 1-5) -------------------------------------------------------


def test_row_shows_number_name_price_stock_and_status_chip(client: AppClient) -> None:
    article_id = add_article("3D-0001", "Kabelhalter", selling_price=Decimal("12.5"))
    add_stock(article_id, 10)
    add_article("3D-0002", "Clip", active=False)

    html = client.get("/articles?inactive=1").text

    first = row_of(html, "3D-0001")
    assert "Kabelhalter" in first
    assert "12,50" in first
    assert re.search(r'<td class="num cell-stock">\s*10\b', first)
    assert f'<span class="chip chip-active">{DE["article.status.active"]}</span>' in first
    second = row_of(html, "3D-0002")
    assert f'<span class="chip chip-inactive">{DE["article.status.inactive"]}</span>' in second


def test_price_and_stock_are_right_aligned_and_missing_price_shows_a_dash(
    client: AppClient,
) -> None:
    add_article("3D-0001", selling_price=None)

    row = row_of(client.get("/articles").text, "3D-0001")

    assert '<td class="num cell-price">' in row
    assert DE["table.empty_value"] in row.split("cell-price")[1].split("</td>")[0]
    assert '<th scope="col" class="num' in client.get("/articles").text


def test_stock_bar_appears_only_with_a_minimum_stock(client: AppClient) -> None:
    with_min = add_article("3D-0001", min_stock=10)
    add_stock(with_min, 4)
    without_min = add_article("3D-0002")
    add_stock(without_min, 4)

    html = client.get("/articles").text

    assert '<meter class="stock-meter"' in row_of(html, "3D-0001")
    assert "<meter" not in row_of(html, "3D-0002")


def test_minimum_stock_is_inherited_from_the_template(client: AppClient) -> None:
    with db() as session, session.begin():
        template = ArticleTemplate(name="Halter", min_stock=8, created_at=datetime(2026, 1, 1))
        session.add(template)
        session.flush()
        template_id = template.id
    variant = add_article("3D-0001", template_id=template_id, combo_key="1:1")
    add_stock(variant, 2)

    assert '<meter class="stock-meter"' in row_of(client.get("/articles").text, "3D-0001")


def test_low_stock_is_marked_with_text_not_only_colour(client: AppClient) -> None:
    low = add_article("3D-0001", min_stock=10)
    add_stock(low, 4)
    enough = add_article("3D-0002", min_stock=10)
    add_stock(enough, 10)

    html = client.get("/articles").text

    assert DE["articles.stock.low"] in row_of(html, "3D-0001")
    assert DE["articles.stock.low"] not in row_of(html, "3D-0002")


def test_stock_in_the_list_is_the_sum_of_the_movements(client: AppClient) -> None:
    article_id = add_article("3D-0001")
    add_stock(article_id, 10)
    add_stock(article_id, -3)

    row = row_of(client.get("/articles").text, "3D-0001")

    assert re.search(r'<td class="num cell-stock">\s*7\b', row)


# --- Sorting (cases 6-9) ----------------------------------------------------------------------


@pytest.fixture
def three_articles(client: AppClient) -> None:
    a = add_article("3D-0002", "Zange", selling_price=Decimal("5"))
    b = add_article("3D-0010", "Axt", selling_price=Decimal("20"))
    c = add_article("3D-0001", "Messer", selling_price=None, active=False)
    add_stock(a, 3)
    add_stock(b, 9)
    add_stock(c, 1)


def test_default_order_is_by_number(client: AppClient, three_articles: None) -> None:
    assert numbers(client, "?inactive=1") == ["3D-0001", "3D-0002", "3D-0010"]


@pytest.mark.parametrize(
    ("sort", "ascending"),
    [
        ("number", ["3D-0001", "3D-0002", "3D-0010"]),
        ("name", ["3D-0010", "3D-0001", "3D-0002"]),
        ("price", ["3D-0002", "3D-0010", "3D-0001"]),
        ("stock", ["3D-0001", "3D-0002", "3D-0010"]),
        ("status", ["3D-0001", "3D-0002", "3D-0010"]),
    ],
)
def test_sorting_by_each_column_ascending_and_descending(
    client: AppClient, three_articles: None, sort: str, ascending: list[str]
) -> None:
    assert numbers(client, f"?inactive=1&sort={sort}&dir=asc") == ascending
    descending = numbers(client, f"?inactive=1&sort={sort}&dir=desc")
    if sort == "price":
        # Articles without a price stay at the end in both directions.
        assert descending == ["3D-0010", "3D-0002", "3D-0001"]
    elif sort == "status":
        assert descending[-1] == "3D-0001"
    else:
        assert descending == ascending[::-1]


def test_header_links_toggle_the_direction_and_mark_the_active_column(
    client: AppClient, three_articles: None
) -> None:
    html = client.get("/articles?sort=name&dir=asc").text

    assert 'aria-sort="ascending"' in html
    assert 'href="/articles?sort=name&amp;dir=desc"' in html
    assert 'href="/articles?sort=price&amp;dir=asc"' in html
    assert html.count('aria-sort="ascending"') == 1
    assert 'aria-sort="none"' in html
    assert "sort-active" in html
    desc = client.get("/articles?sort=name&dir=desc").text
    assert 'aria-sort="descending"' in desc
    assert 'href="/articles?sort=name&amp;dir=asc"' in desc


def test_a_click_on_an_inactive_column_starts_ascending(
    client: AppClient, three_articles: None
) -> None:
    html = client.get("/articles").text

    # Without a choice the list is by number ascending, so that column offers "descending".
    assert 'href="/articles?sort=number&amp;dir=desc"' in html
    assert 'href="/articles?sort=stock&amp;dir=asc"' in html


@pytest.mark.parametrize(
    "query", ["?sort=bogus", "?dir=sideways", "?sort=name&dir=up", "?sort=%27%3B--&dir=asc"]
)
def test_invalid_sort_values_are_ignored(
    client: AppClient, three_articles: None, query: str
) -> None:
    response = client.get("/articles" + query)

    assert response.status_code == 200
    assert numbers(client, query) == ["3D-0002", "3D-0010"]


def test_sorting_is_kept_when_switching_inactive(client: AppClient, three_articles: None) -> None:
    html = client.get("/articles?sort=name&dir=desc").text

    assert 'href="/articles?inactive=1&amp;sort=name&amp;dir=desc"' in html
    shown = client.get("/articles?inactive=1&sort=name&dir=desc").text
    assert 'href="/articles?sort=name&amp;dir=desc"' in shown
    assert 'href="/articles?inactive=1&amp;sort=price&amp;dir=asc"' in shown


# --- Action icons (cases 10-11) ---------------------------------------------------------------


def test_admin_and_employee_see_view_and_edit_icons(client: AppClient, employee: AppClient) -> None:
    article_id = add_article("3D-0001")

    for who in (client, employee):
        row = row_of(who.get("/articles").text, "3D-0001")
        assert f'href="/articles/{article_id}"' in row
        assert f'href="/articles/{article_id}/edit"' in row


def test_reader_sees_only_the_view_icon(client: AppClient, reader: AppClient) -> None:
    article_id = add_article("3D-0001")

    html = reader.get("/articles").text

    assert f'href="/articles/{article_id}"' in row_of(html, "3D-0001")
    assert "/edit" not in html


def test_icons_have_tooltip_and_screen_reader_text_and_are_inline_graphics(
    client: AppClient,
) -> None:
    add_article("3D-0001")

    row = row_of(client.get("/articles").text, "3D-0001")

    for key in ("table.action.view", "table.action.edit"):
        label = DE[key]
        assert f'title="{label}"' in row
        assert f'<span class="visually-hidden">{label}</span>' in row
    assert row.count("<svg") == 2
    assert row.count('aria-hidden="true"') >= 2
    # No picture files and no other servers
    assert "<img" not in row
    assert "http" not in re.sub(r'xmlns="http://www.w3.org/2000/svg"', "", row)


def test_action_column_is_the_first_one(client: AppClient) -> None:
    add_article("3D-0001")

    row = row_of(client.get("/articles").text, "3D-0001")

    assert row.index("row-actions") < row.index("3D-0001")


# --- Building block (case 12) -----------------------------------------------------------------


def test_table_is_a_card_with_the_shared_block_classes(client: AppClient) -> None:
    add_article("3D-0001")

    html = client.get("/articles").text

    assert '<article class="data-card">' in html
    assert '<table class="data-table">' in html
    assert (APP_DIR / "templates" / "_data_table.html").exists()


def test_texts_exist_in_german_and_english() -> None:
    for key in (
        "table.empty_value",
        "table.action.view",
        "table.action.edit",
        "table.sort_by",
        "articles.col.stock",
        "articles.stock.low",
    ):
        assert DE[key]
        assert EN[key]
