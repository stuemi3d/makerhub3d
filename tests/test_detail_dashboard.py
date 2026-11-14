"""Article detail and dashboard in the new style (issue #77, test cases 1-11). Invented data.

The test case numbers refer to the comment on the issue. Existing tests of the detail page
(tiles, source notes, buttons) and of the dashboard figures keep checking the unchanged parts;
the browser tests (tests/browser/test_detail_dashboard.py) check case 12.
"""

import json
import re
from pathlib import Path

import pytest

from app.main import create_app
from tests.auth import READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.test_stock import add_article, add_movement

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
HX = {"HX-Request": "true"}


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


def low_rows(client: AppClient, query: str = "") -> list[str]:
    html = client.get("/" + query).text
    section = re.search(r'<section id="low-stock".*?</section>', html, re.DOTALL)
    assert section
    return re.findall(r'<td class="cell-strong">(.*?)</td>', section.group(0))


def three_low_articles() -> None:
    add_movement(add_article("A-1", name="Zange", min_stock=10), 2)  # short by 8
    add_movement(add_article("A-2", name="Axt", min_stock=4), 3)  # short by 1
    add_movement(add_article("A-3", name="Mutter", min_stock=20), 10)  # short by 10


# --- Detail (cases 1-7) -----------------------------------------------------------------------


def test_detail_status_is_a_chip_and_the_old_badge_is_gone(client: AppClient) -> None:
    active = add_article("A-1")
    inactive = add_article("A-2", active=False)

    first = client.get(f"/articles/{active}").text
    second = client.get(f"/articles/{inactive}").text

    assert f'<span class="chip chip-active">{DE["article.status.active"]}</span>' in first
    assert f'<span class="chip chip-inactive">{DE["article.status.inactive"]}</span>' in second
    assert 'class="badge' not in first + second


def test_detail_keeps_card_tiles_and_notes_in_one_refreshable_region(client: AppClient) -> None:
    article_id = add_article("A-1", name="Kabelhalter")

    html = client.get(f"/articles/{article_id}").text

    region = re.search(r'<div id="article-detail"[^>]*data-popup-refresh[^>]*>', html)
    assert region
    body = html[html.index(region.group(0)) :]
    assert '<article class="detail-card">' in body
    assert 'class="tiles"' in body
    assert "<h1>A-1 · Kabelhalter</h1>" in body
    # The path at the top is part of it, so a changed number shows there too (case 5).
    assert 'aria-label="breadcrumb"' in body


def test_edit_button_opens_the_pop_up_and_keeps_the_normal_address(client: AppClient) -> None:
    article_id = add_article("A-1")

    html = client.get(f"/articles/{article_id}").text

    button = re.search(rf'<a [^>]*href="/articles/{article_id}/edit"[^>]*>', html)
    assert button
    assert f'hx-get="/articles/{article_id}/edit"' in button.group(0)
    assert "data-popup" in button.group(0)
    assert 'role="button"' in button.group(0)
    assert client.get(f"/articles/{article_id}/edit").status_code == 200


def test_after_saving_in_the_pop_up_detail_and_path_show_the_new_values(
    client: AppClient,
) -> None:
    article_id = add_article("A-1", name="Kabelhalter")

    saved = client.post(
        f"/articles/{article_id}", data={"number": "A-9", "name": "Kabelclip"}, headers=HX
    )
    html = client.get(f"/articles/{article_id}").text

    assert saved.status_code == 204
    assert "<h1>A-9 · Kabelclip</h1>" in html
    assert re.search(r"<li>A-9</li>", html)
    assert "Kabelhalter" not in html


def test_pop_up_errors_on_the_detail_stay_in_the_pop_up(client: AppClient) -> None:
    article_id = add_article("A-1", name="Kabelhalter")

    response = client.post(
        f"/articles/{article_id}", data={"number": "A-1", "name": ""}, headers=HX
    )

    assert response.status_code == 422
    assert "<html" not in response.text
    assert 'aria-invalid="true"' in response.text


def test_other_detail_buttons_work_as_before(client: AppClient) -> None:
    article_id = add_article("A-1")

    html = client.get(f"/articles/{article_id}").text

    assert f'action="/articles/{article_id}/deactivate"' in html
    assert f'href="/articles/{article_id}/delete"' in html
    assert client.get(f"/articles/{article_id}/delete").status_code == 200
    off = client.post(f"/articles/{article_id}/deactivate", data={}, follow_redirects=False)
    assert off.status_code == 303


def test_reader_sees_no_edit_button_and_cannot_use_the_pop_up(
    client: AppClient, reader: AppClient
) -> None:
    article_id = add_article("A-1")

    html = reader.get(f"/articles/{article_id}").text

    assert "/edit" not in html
    assert "hx-get" not in html
    assert reader.get(f"/articles/{article_id}/edit", headers=HX).status_code == 403
    denied = reader.post(f"/articles/{article_id}", data={"number": "A-1", "name": "X"}, headers=HX)
    assert denied.status_code == 403


# --- Dashboard (cases 8-11) -------------------------------------------------------------------


def test_dashboard_figures_use_the_card_style(client: AppClient) -> None:
    add_movement(add_article("A-1", min_stock=5), 2)

    html = client.get("/").text

    assert html.count('<article class="stat">') == 3
    assert html.count('class="stat-value"') == 3


def test_low_stock_table_uses_the_table_block(client: AppClient) -> None:
    article_id = add_article("A-1", name="Kabelhalter", min_stock=5)
    add_movement(article_id, 3)

    html = client.get("/").text
    section = re.search(r'<section id="low-stock".*?</section>', html, re.DOTALL).group(0)

    assert '<article class="data-card">' in section
    assert 'class="data-table"' in section
    for key in ("article.field.number", "article.field.name", "stock.col.stock"):
        assert DE[key] in section
    assert DE["dashboard.low.col.shortfall"] in section
    assert '<th scope="col" class="num' in section
    assert re.search(r'<td class="num cell-shortfall">2</td>', section)
    link = re.search(r'<a class="icon-btn" href="([^"]+)"', section)
    assert link
    assert link.group(1) == f"/articles/{article_id}/stock"
    assert DE["table.action.view"] in section
    # Nothing here edits, so no pop-up attributes in the table.
    assert "hx-get" not in section


def test_default_order_is_the_biggest_shortfall_first(client: AppClient) -> None:
    three_low_articles()

    assert low_rows(client) == ["A-3", "A-1", "A-2"]


@pytest.mark.parametrize(
    ("sort", "direction", "expected"),
    [
        ("number", "asc", ["A-1", "A-2", "A-3"]),
        ("number", "desc", ["A-3", "A-2", "A-1"]),
        ("name", "asc", ["A-2", "A-3", "A-1"]),  # Axt, Mutter, Zange
        ("stock", "asc", ["A-1", "A-2", "A-3"]),  # 2, 3, 10
        ("stock", "desc", ["A-3", "A-2", "A-1"]),
        ("min_stock", "asc", ["A-2", "A-1", "A-3"]),  # 4, 10, 20
        ("shortfall", "asc", ["A-2", "A-1", "A-3"]),  # 1, 8, 10
        ("shortfall", "desc", ["A-3", "A-1", "A-2"]),
    ],
)
def test_columns_sort_through_the_address(
    client: AppClient, sort: str, direction: str, expected: list[str]
) -> None:
    three_low_articles()

    assert low_rows(client, f"?sort={sort}&dir={direction}") == expected


@pytest.mark.parametrize("query", ["?sort=drop+table", "?sort=name&dir=sideways", "?dir=asc"])
def test_an_invalid_sort_falls_back_to_the_default_order(client: AppClient, query: str) -> None:
    three_low_articles()

    response = client.get("/" + query)

    assert response.status_code == 200
    assert low_rows(client, query) == ["A-3", "A-1", "A-2"]


def test_sort_links_mark_the_sorted_column_and_reverse_it(client: AppClient) -> None:
    three_low_articles()

    default = client.get("/").text
    by_name = client.get("/?sort=name&dir=asc").text

    assert 'aria-sort="descending"' in default  # shortfall, as shown
    assert 'href="/?sort=shortfall&amp;dir=asc"' in default
    assert 'href="/?sort=name&amp;dir=desc"' in by_name
    assert by_name.count('aria-sort="ascending"') == 1


def test_empty_states_stay_and_the_create_button_opens_the_pop_up(client: AppClient) -> None:
    html = client.get("/").text

    assert DE["dashboard.low.no_articles"] in html
    button = re.search(r'<a [^>]*href="/articles/new"[^>]*>', html)
    assert button
    assert 'hx-get="/articles/new"' in button.group(0)
    assert "data-popup" in button.group(0)
    add_article("A-1")
    assert DE["dashboard.low.ok"] in client.get("/").text


def test_reader_sees_the_dashboard_without_create_button(reader: AppClient) -> None:
    html = reader.get("/").text

    assert DE["dashboard.low.no_articles"] in html
    assert "/articles/new" not in html
    assert "hx-get" not in html


def test_dashboard_still_needs_a_login() -> None:
    with AppClient(create_app()) as visitor:
        response = visitor.get("/", follow_redirects=False)

    assert response.status_code in (303, 401, 403)
    assert DE["dashboard.low.title"] not in response.text
