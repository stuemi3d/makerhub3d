"""Sales by hand (issue #201, test cases 1-13). All data is invented.

The test case numbers refer to the comment on the issue. The pop-ups in the browser (desktop
and phone view) are checked in tests/browser/test_sales.py.
"""

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.article_purge import counts
from app.main import create_app
from app.models import Article, ChangeLogEntry, Sale, StockMovement
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_stock import DE, add_article, add_movement, db, field_error, stock

HX = {"HX-Request": "true"}


def sale_form(article: int, **overrides: str) -> dict[str, str]:
    form = {
        "article_id": str(article),
        "quantity": "3",
        "price": "12,50",
        "date": "",
        "note": "Markt am Samstag",
    }
    form.update(overrides)
    return form


def sell(client: AppClient, article: int, **overrides: str):
    return client.post("/sales/new", data=sale_form(article, **overrides), follow_redirects=False)


def sales() -> list[Sale]:
    with db() as session:
        return list(session.query(Sale).order_by(Sale.id))


def movements() -> list[StockMovement]:
    with db() as session:
        return list(session.query(StockMovement).order_by(StockMovement.id))


def stocked_article(amount: int = 10, **values: object) -> int:
    article_id = add_article(**values)
    add_movement(article_id, amount)
    return article_id


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


# --- Case 1: selling lowers the stock ------------------------------------------------------


def test_selling_lowers_the_stock_and_is_listed(client: AppClient) -> None:
    article_id = stocked_article(10)

    response = sell(client, article_id)

    assert response.status_code == 303
    assert stock(article_id) == 7
    [sale] = sales()
    assert (sale.quantity, sale.unit_price, sale.source) == (3, Decimal("12.50"), "hand")
    assert sale.note == "Markt am Samstag"
    assert sale.booked_on == date.today()
    assert not sale.cancelled
    [_, movement] = movements()
    assert (movement.kind, movement.quantity, movement.id) == ("sale", -3, sale.movement_id)
    page = client.get("/sales").text
    assert "3D-0001" in page and "12,50" in page and "37,50" in page


# --- Cases 2 and 3: the price -------------------------------------------------------------


def test_the_form_prefills_the_selling_price_of_the_article(client: AppClient) -> None:
    article_id = stocked_article(selling_price=Decimal("9.90"))

    html = client.get(f"/sales/new?article={article_id}", headers=HX).text

    assert re.search(r'name="price"[^>]*value="9,90"', html)
    assert 'name="note"' in html and "<html" not in html


def test_later_price_change_does_not_touch_old_sales(client: AppClient) -> None:
    article_id = stocked_article(selling_price=Decimal("9.90"))
    sell(client, article_id, price="9,90")
    with db() as session, session.begin():
        session.get(Article, article_id).selling_price = Decimal("20")

    assert sales()[0].unit_price == Decimal("9.90")


def test_price_zero_is_allowed(client: AppClient) -> None:
    article_id = stocked_article()

    assert sell(client, article_id, price="0").status_code == 303
    assert sales()[0].unit_price == 0


# --- Cases 4 and 5: checks ---------------------------------------------------------------


def test_cannot_sell_more_than_the_stock(client: AppClient) -> None:
    article_id = stocked_article(2)

    response = sell(client, article_id, quantity="3")

    assert response.status_code == 422
    assert field_error(response.text, "quantity")
    assert sales() == [] and stock(article_id) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", "0"),
        ("quantity", "-1"),
        ("quantity", "1,5"),
        ("quantity", ""),
        ("price", ""),
        ("price", "-1"),
        ("price", "abc"),
        ("price", "1,234"),
        ("date", "31.02.2026"),
        ("date", (date.today() + timedelta(days=1)).isoformat()),
        ("article_id", ""),
        ("article_id", "9999"),
        ("note", "x" * 201),
    ],
)
def test_invalid_input_is_refused_and_nothing_is_booked(
    client: AppClient, field: str, value: str
) -> None:
    article_id = stocked_article()

    response = sell(client, article_id, **{field: value})

    assert response.status_code == 422
    assert field_error(response.text, field)
    assert sales() == [] and stock(article_id) == 10


def test_inactive_article_cannot_be_sold(client: AppClient) -> None:
    article_id = stocked_article(active=False)

    response = sell(client, article_id)

    assert response.status_code == 422 and field_error(response.text, "article_id")
    assert sales() == []


# --- Case 6: cancelling -------------------------------------------------------------------


def test_cancelling_brings_the_stock_back_and_keeps_the_sale(client: AppClient) -> None:
    article_id = stocked_article(10)
    sell(client, article_id)
    [sale] = sales()

    page = client.get(f"/sales/{sale.id}/cancel", headers=HX).text
    response = client.post(f"/sales/{sale.id}/cancel", data={}, headers=HX, follow_redirects=False)

    assert "3D-0001" in page
    assert response.status_code == 204
    assert stock(article_id) == 10
    [sale] = sales()
    assert sale.cancelled
    last = movements()[-1]
    assert (last.kind, last.quantity, last.cancels_id) == ("cancellation", 3, sale.movement_id)
    assert DE["sales.state.cancelled"] in client.get("/sales").text


def test_a_sale_can_be_cancelled_only_once(client: AppClient) -> None:
    article_id = stocked_article(10)
    sell(client, article_id)
    [sale] = sales()
    client.post(f"/sales/{sale.id}/cancel", data={}, follow_redirects=False)

    again = client.post(f"/sales/{sale.id}/cancel", data={}, follow_redirects=False)

    assert again.status_code == 422
    assert stock(article_id) == 10 and len(movements()) == 3


def test_unknown_sale_gives_404(client: AppClient) -> None:
    assert client.get("/sales/999/cancel").status_code == 404
    assert client.post("/sales/999/cancel", data={}).status_code == 404


# --- Case 7: stock list ----------------------------------------------------------------------


def test_stock_list_links_the_sale_and_cannot_cancel_it_there(client: AppClient) -> None:
    article_id = stocked_article(10)
    sell(client, article_id)
    [sale] = sales()
    movement_id = sale.movement_id

    html = client.get("/stock/movements").text

    assert DE["stock.kind.sale"] in html
    assert f'href="/sales?article={article_id}"' in html or f"/sales/{sale.id}" in html
    assert f"/stock/movements/{movement_id}/cancel" not in html
    refused = client.post(f"/stock/movements/{movement_id}/cancel", data={})
    assert refused.status_code == 422 and stock(article_id) == 7


# --- Case 8: rights -------------------------------------------------------------------------


def test_employee_may_sell_and_cancel(employee: AppClient) -> None:
    article_id = stocked_article()

    assert sell(employee, article_id).status_code == 303
    [sale] = sales()
    assert employee.post(f"/sales/{sale.id}/cancel", data={}).status_code in (200, 303)
    assert sales()[0].cancelled


def test_reader_sees_the_list_but_cannot_change_anything(reader: AppClient) -> None:
    article_id = stocked_article()
    with db() as session, session.begin():
        session.add(
            Sale(
                article_id=article_id,
                booked_on=date.today(),
                created_at=datetime(2026, 1, 1),
                quantity=1,
                unit_price=Decimal("5"),
                note="",
                source="hand",
                movement_id=add_movement(article_id, -1, "sale", ""),
            )
        )
    [sale] = sales()

    page = reader.get("/sales").text

    assert "3D-0001" in page
    assert "/sales/new" not in page and f"/sales/{sale.id}/cancel" not in page
    assert reader.get("/sales/new").status_code == 403
    assert sell(reader, article_id).status_code == 403
    assert reader.get(f"/sales/{sale.id}/cancel").status_code == 403
    assert reader.post(f"/sales/{sale.id}/cancel", data={}).status_code == 403
    assert not sales()[0].cancelled


def test_everything_needs_a_login() -> None:
    with TestClient(create_app(), follow_redirects=False) as anonymous:
        assert anonymous.get("/sales").status_code in (303, 401)
        assert anonymous.get("/sales/new").status_code in (303, 401)
        assert anonymous.get("/sales/1/cancel").status_code in (303, 401)
        assert anonymous.post("/sales/new", data={}).status_code in (303, 401, 403)


# --- Case 9: change log ---------------------------------------------------------------------


def test_selling_and_cancelling_are_in_the_change_log(client: AppClient) -> None:
    article_id = stocked_article()
    sell(client, article_id)
    [sale] = sales()
    client.post(f"/sales/{sale.id}/cancel", data={}, follow_redirects=False)

    with db() as session:
        entries = [
            (e.action, e.subject, json.loads(e.details))
            for e in session.query(ChangeLogEntry).order_by(ChangeLogEntry.id)
            if e.action.startswith("sale.")
        ]

    assert [e[0] for e in entries] == ["sale.booked", "sale.cancelled"]
    assert entries[0][1] == "3D-0001"
    assert {"field": "quantity", "old": "", "new": "3"} in entries[0][2]
    assert {"field": "price", "old": "", "new": "12.50"} in entries[0][2]
    assert "log.action.sale.booked" in DE and "log.action.sale.cancelled" in DE


# --- Case 10: tile and filter ---------------------------------------------------------------


def test_tile_counts_sales_not_cancelled_and_list_is_filtered(client: AppClient) -> None:
    first = stocked_article(10)
    second = stocked_article(10, number="3D-0002", name="Clip")
    sell(client, first)
    sell(client, first, quantity="1")
    sell(client, second, quantity="2")
    cancelled = sales()[0]
    client.post(f"/sales/{cancelled.id}/cancel", data={}, follow_redirects=False)

    detail = client.get(f"/articles/{first}").text
    section = client.get(f"/articles/{first}/sales", follow_redirects=False)
    filtered = client.get(f"/sales?article={first}").text

    assert re.search(r'href="/articles/\d+/sales"[^>]*>.*?\b1\b', detail, re.DOTALL)
    assert section.status_code == 303 and section.headers["location"] == f"/sales?article={first}"
    in_table = re.findall(r'class="cell-strong"><a [^>]*>([^<]*)</a>', filtered)
    assert in_table == ["3D-0001", "3D-0001"]


# --- Case 11: deleting an article ----------------------------------------------------------


def test_sales_count_for_the_article_deletion_rules(employee: AppClient) -> None:
    article_id = stocked_article()
    sell(employee, article_id)
    with db() as session:
        assert counts(session, article_id)["sales"] == 1


def test_purging_an_article_deletes_its_sales(client: AppClient) -> None:
    article_id = stocked_article()
    other = stocked_article(number="3D-0002", name="Clip")
    sell(client, article_id)
    sell(client, other)

    response = client.post(
        f"/articles/{article_id}/purge", data={"number": "3D-0001"}, follow_redirects=False
    )

    assert response.status_code in (204, 303)
    assert [s.article_id for s in sales()] == [other]


# --- Case 12: migration ---------------------------------------------------------------------


def test_migration_creates_the_sales_table(client: AppClient) -> None:
    from sqlalchemy import inspect

    with db() as session:
        columns = {c["name"] for c in inspect(session.get_bind()).get_columns("sales")}

    assert {
        "article_id",
        "booked_on",
        "created_at",
        "quantity",
        "unit_price",
        "note",
        "source",
        "movement_id",
        "cancelled",
        "user_id",
    } <= columns
