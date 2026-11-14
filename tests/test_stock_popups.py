"""Stock pop-ups and tables (issue #87, part A, test cases 1-16). All data is invented.

The test case numbers refer to the comment on the issue. Opening, closing, focus and the phone
view are checked in tests/browser/test_stock_popups.py; here the server side is checked.
"""

import json
import re
from datetime import date, datetime

import pytest

from app.models import StockMovement
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.test_stock import (
    DE,
    add_article,
    add_movement,
    booking,
    db,
    field_error,
    logged,
    movements,
    stock,
)

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


def is_fragment(html: str) -> bool:
    return "<html" not in html and "<header" not in html and "<form" in html


def opener(html: str, href: str) -> str:
    match = re.search(rf'<a [^>]*href="{re.escape(href)}"[^>]*>', html)
    assert match, f"no link to {href}"
    return match.group(0)


def numbers_in_order(html: str, numbers: list[str]) -> list[str]:
    """The given article numbers in the order they appear in the page."""
    found = [(html.index(n), n) for n in numbers if n in html]
    return [n for _, n in sorted(found)]


# --- Case 1, 2: the pop-up opens from every place --------------------------------------------


def test_booking_links_open_the_pop_up_on_every_page_and_keep_a_normal_address(
    client: AppClient,
) -> None:
    article_id = add_article()
    add_movement(article_id, 5)

    for page in ("/stock", "/stock/movements", f"/articles/{article_id}/stock"):
        html = client.get(page).text
        links = re.findall(r'<a [^>]*href="/stock/new[^"]*"[^>]*>', html)
        assert links, page
        for link in links:
            assert f'hx-get="{re.search(r"href=.([^\"]+)", link).group(1)}"' in link
            assert "data-popup" in link
        assert '<dialog id="popup"' in html


def test_pop_up_is_only_the_form_and_the_page_is_complete(client: AppClient) -> None:
    popup = client.get("/stock/new", headers=HX)
    page = client.get("/stock/new")

    assert popup.status_code == page.status_code == 200
    assert is_fragment(popup.text)
    assert "<html" in page.text and "topbar" in page.text
    assert "HX-Request" in popup.headers["vary"]
    for field in ("article_id", "type", "quantity", "reason", "date"):
        assert f'name="{field}"' in popup.text
        assert f'name="{field}"' in page.text
    assert "hx-post" in popup.text
    assert "hx-post" not in page.text
    assert re.search(r'name="csrf_token" value="[^"]+"', popup.text)
    assert 'id="popup-title"' in popup.text


def test_pop_up_from_an_article_has_the_article_selected(client: AppClient) -> None:
    article_id = add_article()
    add_article("3D-0002", "Clip")

    html = client.get(f"/stock/new?article={article_id}", headers=HX).text

    assert re.search(rf'<option value="{article_id}" selected>', html)


# --- Case 3, 4: saving ------------------------------------------------------------------------


def test_withdrawal_from_the_pop_up_answers_without_a_page_and_changes_the_stock(
    client: AppClient,
) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    response = client.post("/stock/new", data=booking(article_id), headers=HX)

    assert response.status_code == 204
    assert response.content == b""
    assert json.loads(response.headers["hx-trigger"]) == {"popup-saved": {}}
    assert stock(article_id) == 7
    assert re.search(r"3D-0001.*?<td[^>]*>\s*7\s*</td>", client.get("/stock").text, re.DOTALL)


def test_correction_from_the_pop_up_changes_the_stock_and_is_logged(client: AppClient) -> None:
    article_id = add_article()

    response = client.post(
        "/stock/new", data=booking(article_id, type="correction", quantity="+12"), headers=HX
    )

    assert response.status_code == 204
    assert stock(article_id) == 12
    assert logged()


# --- Case 5: errors ---------------------------------------------------------------------------


def test_errors_stay_in_the_pop_up_keep_the_input_and_book_nothing(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 2)

    too_much = client.post("/stock/new", data=booking(article_id, quantity="5"), headers=HX)
    empty = client.post(
        "/stock/new", data=booking(article_id, quantity="", reason="Notiz"), headers=HX
    )

    assert too_much.status_code == empty.status_code == 422
    for response in (too_much, empty):
        assert is_fragment(response.text)
        assert "hx-post" in response.text
    assert field_error(too_much.text, "quantity")
    assert 'value="5"' in too_much.text
    assert field_error(empty.text, "quantity")
    assert 'value="Notiz"' in empty.text
    assert stock(article_id) == 2
    assert len(movements()) == 1


# --- Case 7: without JavaScript ---------------------------------------------------------------


def test_plain_pages_still_redirect_and_show_errors_on_the_page(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    saved = client.post("/stock/new", data=booking(article_id), follow_redirects=False)
    failed = client.post("/stock/new", data=booking(article_id, quantity="99"))

    assert saved.status_code == 303
    assert saved.headers["location"] == "/stock"
    assert failed.status_code == 422
    assert "<html" in failed.text and "hx-post" not in failed.text
    assert field_error(failed.text, "quantity")
    assert stock(article_id) == 7


# --- Case 8: rights, login, token -------------------------------------------------------------


def test_reader_gets_no_button_no_symbol_and_no_booking(
    reader: AppClient, client: AppClient
) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    for page in ("/stock", "/stock/movements", f"/articles/{article_id}/stock"):
        html = reader.get(page).text
        assert "/stock/new" not in html, page
        assert "hx-get" not in html, page
        assert 'action="/stock/movements/' not in html, page
    for headers in ({}, HX):
        assert reader.get("/stock/new", headers=headers).status_code == 403
        response = reader.post("/stock/new", data=booking(article_id), headers=headers)
        assert response.status_code == 403
    assert stock(article_id) == 10


def test_without_login_nothing_is_shown_or_booked() -> None:
    from app.main import create_app

    with AppClient(create_app()) as anonymous:
        for headers in ({}, HX):
            get = anonymous.get("/stock/new", headers=headers, follow_redirects=False)
            post = anonymous.post("/stock/new", data={}, headers=headers, follow_redirects=False)
            assert get.status_code in (303, 401, 403)
            assert post.status_code in (303, 401, 403)
            assert 'name="quantity"' not in get.text


def test_employee_may_use_the_pop_up(client: AppClient) -> None:
    article_id = add_article()
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        assert employee.get("/stock/new", headers=HX).status_code == 200
        response = employee.post(
            "/stock/new", data=booking(article_id, type="correction"), headers=HX
        )
        assert response.status_code == 204


def test_booking_without_a_form_token_is_refused(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    client.csrf_token = None

    response = client.post(
        "/stock/new", data={**booking(article_id), "csrf_token": "falsch"}, headers=HX
    )

    assert response.status_code == 403
    assert stock(article_id) == 10


# --- Case 9, 10: stock list -------------------------------------------------------------------


def seeded_levels(client: AppClient) -> None:
    for number, name, amount in (("3D-0001", "Zeta", 5), ("3D-0002", "Alpha", 20)):
        add_movement(add_article(number, name), amount)
    add_article("3D-0003", "Mitte", min_stock=10)


def test_stock_list_sorts_by_each_column_in_both_directions(client: AppClient) -> None:
    seeded_levels(client)
    order = ["3D-0001", "3D-0002", "3D-0003"]

    def sorted_by(key: str, direction: str) -> list[str]:
        return numbers_in_order(client.get(f"/stock?sort={key}&dir={direction}").text, order)

    assert sorted_by("number", "desc") == ["3D-0003", "3D-0002", "3D-0001"]
    assert sorted_by("name", "asc") == ["3D-0002", "3D-0003", "3D-0001"]
    assert sorted_by("stock", "desc") == ["3D-0002", "3D-0001", "3D-0003"]
    assert sorted_by("min_stock", "desc")[0] == "3D-0003"
    html = client.get("/stock?sort=stock&dir=asc").text
    assert 'aria-sort="ascending"' in html
    assert numbers_in_order(client.get("/stock").text, order) == order


def test_invalid_sorting_is_ignored_and_not_echoed(client: AppClient) -> None:
    seeded_levels(client)

    for query in ("sort=<script>&dir=asc", "sort=stock&dir=sideways", "sort=drop&dir=desc"):
        response = client.get(f"/stock?{query}")
        assert response.status_code == 200
        assert "<script>" not in response.text
        assert numbers_in_order(response.text, ["3D-0001", "3D-0002", "3D-0003"])[0] == "3D-0001"


def test_sorting_stays_when_toggling_inactive_and_after_booking(client: AppClient) -> None:
    seeded_levels(client)
    add_article("3D-0004", "Alt", active=False)

    html = client.get("/stock?sort=stock&dir=desc").text

    toggle = opener(html, "/stock?inactive=1&amp;sort=stock&amp;dir=desc")
    assert toggle
    assert 'id="stock-list"' in html and "data-popup-refresh" in html
    assert "3D-0004" not in html
    assert "3D-0004" in client.get("/stock?inactive=1&sort=stock&dir=desc").text


def test_stock_rows_have_chips_and_symbols(client: AppClient) -> None:
    low = add_article("3D-0001", "Knapp", min_stock=10)
    add_movement(low, 3)
    add_article("3D-0002", "Alt", active=False)
    add_article("3D-0003", "Normal")

    html = client.get("/stock?inactive=1").text

    row = {
        n: re.search(rf"<tr>(?:(?!</tr>).)*{n}.*?</tr>", html, re.DOTALL).group(0)
        for n in ("3D-0001", "3D-0002", "3D-0003")
    }
    assert DE["stock.low"] in row["3D-0001"] and "chip" in row["3D-0001"]
    assert DE["article.status.inactive"] in row["3D-0002"]
    assert DE["article.status.active"] in row["3D-0003"]
    assert 'class="icon-btn"' in row["3D-0001"]
    assert f'href="/articles/{low}/stock"' in row["3D-0001"]
    book_link = opener(row["3D-0001"], f"/stock/new?article={low}")
    assert "data-popup" in book_link
    assert DE["table.action.book"] in row["3D-0001"]


# --- Case 11-14: movements list ---------------------------------------------------------------


def seeded_movements() -> list[int]:
    first = add_article("3D-0001", "Eins")
    second = add_article("3D-0002", "Zwei")
    older = add_movement(first, 5, reason="Alpha")
    newer = add_movement(second, 50, reason="Beta")
    with db() as session, session.begin():
        movement = session.get(StockMovement, older)
        movement.booked_on = date(2025, 12, 1)
        movement.created_at = datetime(2025, 12, 1, 9, 0)
    return [older, newer]


def test_movements_sort_by_date_article_and_quantity(client: AppClient) -> None:
    seeded_movements()

    def order(query: str) -> list[str]:
        html = client.get(f"/stock/movements?{query}").text
        return numbers_in_order(html, ["Alpha", "Beta"])

    assert order("sort=quantity&dir=desc") == ["Beta", "Alpha"]
    assert order("sort=quantity&dir=asc") == ["Alpha", "Beta"]
    assert order("sort=article&dir=desc") == ["Beta", "Alpha"]
    assert order("sort=date&dir=asc") == ["Alpha", "Beta"]
    # Default: newest first, as before.
    assert order("") == ["Beta", "Alpha"]
    assert 'aria-sort="descending"' in client.get("/stock/movements").text


def test_movements_sorting_keeps_the_article_filter_and_ignores_bad_values(
    client: AppClient,
) -> None:
    first = add_article("3D-0001", "Eins")
    add_article("3D-0002", "Zwei")
    add_movement(first, 5, reason="Alpha")

    html = client.get(f"/stock/movements?article={first}&sort=quantity&dir=asc").text

    assert f"article={first}" in re.search(r'<a href="([^"]*sort=quantity[^"]*)"', html).group(1)
    assert client.get("/stock/movements?sort=<b>&dir=up").status_code == 200
    assert "<b>" not in client.get("/stock/movements?sort=<b>&dir=up").text


def test_movements_show_kind_and_cancelled_as_chips(client: AppClient) -> None:
    article_id = add_article()
    booked = add_movement(article_id, -3, reason="Muster")
    add_movement(article_id, 3, kind="cancellation", reason="", cancels_id=booked)
    add_movement(article_id, 8, kind="production", reason="Druck")

    html = client.get("/stock/movements").text

    for kind in ("correction", "cancellation", "production"):
        assert re.search(rf'<span class="chip[^"]*">{DE["stock.kind." + kind]}</span>', html)
    assert re.search(rf'<span class="chip[^"]*">{DE["stock.cancelled"]}</span>', html)
    assert f"{DE['stock.reference']} #{booked}" in html


def test_cancelling_is_a_secure_form_with_a_symbol(client: AppClient) -> None:
    article_id = add_article()
    booked = add_movement(article_id, -3, reason="Muster")
    done = add_movement(article_id, -1, reason="Schon weg")
    add_movement(article_id, 1, kind="cancellation", reason="", cancels_id=done)

    html = client.get("/stock/movements").text

    forms = re.findall(
        r'<form class="icon-form" method="post"'
        r' action="/stock/movements/(\d+)/cancel"[^>]*>(.*?)</form>',
        html,
        re.DOTALL,
    )
    assert [form_id for form_id, _ in forms] == [str(booked)]
    body = forms[0][1]
    assert 'name="csrf_token"' in body
    assert "<svg" in body and DE["stock.cancel"] in body
    cancelled = client.post(f"/stock/movements/{booked}/cancel", data={}, follow_redirects=False)
    assert cancelled.status_code == 303
    assert f"/stock/movements/{done}/cancel" not in client.get("/stock/movements").text


def test_article_stock_page_uses_the_same_table(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 4, reason="Alpha")
    add_movement(article_id, 9, reason="Beta")

    html = client.get(f"/articles/{article_id}/stock?sort=quantity&dir=desc").text

    assert numbers_in_order(html, ["Alpha", "Beta"]) == ["Beta", "Alpha"]
    assert 'class="data-table"' in html
    assert 'id="movements-list"' in html and "data-popup-refresh" in html
    assert f"/articles/{article_id}/stock?" in html
    assert opener(html, f"/stock/new?article={article_id}")


# --- Case 16: safe output ---------------------------------------------------------------------


def test_reasons_are_escaped_and_nothing_is_loaded_from_other_servers(client: AppClient) -> None:
    article_id = add_article("3D-0001", "<i>Name</i>")
    add_movement(article_id, 5, reason="<script>alert(1)</script>")

    for page in ("/stock", "/stock/movements", "/stock/new", f"/articles/{article_id}/stock"):
        html = client.get(page).text
        assert "<script>alert(1)</script>" not in html
        assert "<i>Name</i>" not in html
        assert not re.findall(r'(?:src|href)="(?:https?:)?//', html)


def test_article_stock_level_is_inside_the_refreshed_region_and_symbols_name_the_article(
    client: AppClient,
) -> None:
    article_id = add_article()
    booked = add_movement(article_id, 4)

    html = client.get(f"/articles/{article_id}/stock").text
    region = html[html.index('id="movements-list"') :]
    assert f"{DE['stock.col.stock']}: 4" in region

    movements_html = client.get("/stock/movements").text
    assert f"{DE['table.action.cancel']} 3D-0001" in movements_html
    assert f"/stock/movements/{booked}/cancel" in movements_html
    assert f"{DE['table.action.book']} 3D-0001" in client.get("/stock").text
