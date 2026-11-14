"""Dashboard, first version: key figures and articles below the minimum stock (issue #12).

All data is invented. The test case numbers refer to the comment on the issue.
"""

import json
import re
from pathlib import Path

import pytest

from app.main import create_app
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_stock import add_article, add_movement, book, cancel

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))


def tile(html: str, key: str) -> str:
    """The figure shown in the tile with the given label key."""
    match = re.search(
        rf'<article class="stat">\s*<p class="stat-label">{re.escape(DE[key])}</p>'
        rf'\s*<p class="stat-value">(.*?)</p>',
        html,
        re.DOTALL,
    )
    assert match, f"no tile {key}"
    return match.group(1).strip()


def low_list(html: str) -> list[str]:
    """Article numbers in the list 'below minimum stock', top to bottom."""
    section = re.search(r'<section id="low-stock".*?</section>', html, re.DOTALL)
    assert section, "no low-stock section"
    return re.findall(r'<td class="cell-strong">(.*?)</td>', section.group(0))


def section_html(html: str) -> str:
    match = re.search(r'<section id="low-stock".*?</section>', html, re.DOTALL)
    assert match
    return match.group(0)


# --- Test cases 1-4: key figures ------------------------------------------------------------


def test_tile_active_articles_counts_only_active(client: AppClient) -> None:
    add_article("A-1")
    add_article("A-2")
    add_article("A-3")
    add_article("A-4", active=False)

    assert tile(client.get("/").text, "dashboard.tile.articles") == "3"


def test_tile_total_stock_sums_active_articles_only(client: AppClient) -> None:
    add_movement(add_article("A-1"), 10)
    add_movement(add_article("A-2"), 5)
    add_article("A-3")
    add_movement(add_article("A-4", active=False), 100)

    assert tile(client.get("/").text, "dashboard.tile.stock") == "15"


def test_tile_below_minimum_matches_the_list(client: AppClient) -> None:
    first = add_article("A-1", min_stock=5)
    second = add_article("A-2", min_stock=2)
    add_movement(first, 3)
    add_movement(second, 9)

    html = client.get("/").text

    assert tile(html, "dashboard.tile.low") == "1"
    assert low_list(html) == ["A-1"]


def test_cancelled_withdrawal_restores_the_figures(client: AppClient) -> None:
    article_id = add_article("A-1", min_stock=5)
    add_movement(article_id, 6)
    assert book(client, article_id, quantity="2").status_code == 303
    assert tile(client.get("/").text, "dashboard.tile.stock") == "4"
    assert tile(client.get("/").text, "dashboard.tile.low") == "1"

    assert cancel(client, 2).status_code == 303

    html = client.get("/").text
    assert tile(html, "dashboard.tile.stock") == "6"
    assert tile(html, "dashboard.tile.low") == "0"


# --- Test cases 5-9: the list ---------------------------------------------------------------


def test_article_below_minimum_is_listed_with_details(client: AppClient) -> None:
    article_id = add_article("A-1", name="Kabelhalter", min_stock=5)
    add_movement(article_id, 3)

    section = section_html(client.get("/").text)

    assert low_list(client.get("/").text) == ["A-1"]
    assert "Kabelhalter" in section
    assert f'href="/articles/{article_id}/stock"' in section
    assert re.search(r'<td class="num cell-stock">3</td>', section)
    assert re.search(r'<td class="num cell-min">5</td>', section)


def test_stock_equal_to_minimum_is_not_listed(client: AppClient) -> None:
    add_movement(add_article("A-1", min_stock=5), 5)

    assert low_list(client.get("/").text) == []


@pytest.mark.parametrize("stock", [0, 7])
def test_article_without_minimum_is_never_listed(client: AppClient, stock: int) -> None:
    article_id = add_article("A-1")
    if stock:
        add_movement(article_id, stock)

    assert low_list(client.get("/").text) == []


def test_inactive_article_is_not_listed(client: AppClient) -> None:
    add_article("A-1", min_stock=5, active=False)

    html = client.get("/").text

    assert low_list(html) == []
    assert tile(html, "dashboard.tile.low") == "0"


def test_biggest_shortfall_comes_first(client: AppClient) -> None:
    small = add_article("A-1", min_stock=5)  # stock 4, short by 1
    big = add_article("A-2", min_stock=10)  # stock 1, short by 9
    middle = add_article("A-3", min_stock=6)  # stock 2, short by 4
    add_movement(small, 4)
    add_movement(big, 1)
    add_movement(middle, 2)

    assert low_list(client.get("/").text) == ["A-2", "A-3", "A-1"]


# --- Test case 10: hints --------------------------------------------------------------------


def test_all_fine_hint_when_nothing_is_below_minimum(client: AppClient) -> None:
    add_movement(add_article("A-1", min_stock=2), 5)

    section = section_html(client.get("/").text)

    assert DE["dashboard.low.ok"] in section
    assert DE["dashboard.low.no_articles"] not in section


def test_no_articles_hint_links_to_creating_one(client: AppClient) -> None:
    section = section_html(client.get("/").text)

    assert DE["dashboard.low.no_articles"] in section
    assert 'href="/articles/new"' in section


def test_reader_gets_no_create_link(client: AppClient) -> None:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        section = section_html(reader.get("/").text)

    assert DE["dashboard.low.no_articles"] in section
    assert 'href="/articles/new"' not in section


# --- Test cases 11-12: rights ---------------------------------------------------------------


def test_dashboard_needs_login(client: AppClient) -> None:
    with AppClient(create_app()) as visitor:
        response = visitor.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("role", ["read_only", "employee"])
def test_every_role_sees_the_dashboard(client: AppClient, role: str) -> None:
    name = READER_NAME if role == "read_only" else EMPLOYEE_NAME
    add_user(name, role)
    add_article("A-1", min_stock=1)

    with client_as(name) as user:
        response = user.get("/")

    assert response.status_code == 200
    assert tile(response.text, "dashboard.tile.articles") == "1"


def test_admin_sees_the_dashboard(client: AppClient) -> None:
    assert tile(client.get("/").text, "dashboard.tile.articles") == "0"
