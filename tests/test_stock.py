"""Stock movements, stock levels, manual booking and cancelling (issue #11, test cases 1-22).

All data is invented. The test case numbers refer to the comment on the issue.
"""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import Article, ChangeLogEntry, StockMovement, User
from app.stock import stock_of
from tests.auth import (
    EMPLOYEE_NAME,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
    signed_in_client,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


def add_article(number: str = "3D-0001", name: str = "Kabelhalter", **values: object) -> int:
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


def add_movement(
    article_id: int,
    quantity: int,
    kind: str = "correction",
    reason: str = "Startbestand",
    cancels_id: int | None = None,
) -> int:
    """A movement written straight into the database (like later production or sales would)."""
    with db() as session, session.begin():
        movement = StockMovement(
            article_id=article_id,
            booked_on=date(2026, 1, 2),
            created_at=datetime(2026, 1, 2, 10, 0),
            quantity=quantity,
            kind=kind,
            reason=reason,
            cancels_id=cancels_id,
            user_id=None,
        )
        session.add(movement)
        session.flush()
        return movement.id


def movements() -> list[StockMovement]:
    with db() as session:
        return list(session.query(StockMovement).order_by(StockMovement.id))


def stock(article_id: int) -> int:
    with db() as session:
        return stock_of(session, article_id)


def booking(article_id: int, **overrides: str) -> dict[str, str]:
    form = {
        "article_id": str(article_id),
        "type": "withdrawal",
        "quantity": "3",
        "reason": "Muster verschenkt",
        "date": "",
    }
    form.update(overrides)
    return form


def book(client: AppClient, article_id: int, **overrides: str):
    return client.post("/stock/new", data=booking(article_id, **overrides), follow_redirects=False)


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


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


# --- Test cases 1-3: stock level ------------------------------------------------------------


def test_new_article_has_stock_zero(client: AppClient) -> None:
    article_id = add_article()

    assert stock(article_id) == 0
    assert re.search(r"3D-0001.*?<td[^>]*>\s*0\s*</td>", client.get("/stock").text, re.DOTALL)


def test_stock_is_the_sum_of_all_movements_of_the_article(client: AppClient) -> None:
    first = add_article()
    second = add_article("3D-0002", "Clip")
    add_movement(first, 10)
    add_movement(first, -4)
    add_movement(second, 99)

    assert stock(first) == 6
    assert stock(second) == 99


def test_stock_page_shows_the_stock_of_every_article(client: AppClient) -> None:
    first = add_article()
    add_movement(first, 7)

    html = client.get("/stock").text

    assert re.search(r"3D-0001.*?<td[^>]*>\s*7\s*</td>", html, re.DOTALL)
    assert "Kabelhalter" in html


def test_article_below_minimum_is_marked_and_others_are_not(client: AppClient) -> None:
    low = add_article("3D-0001", "Knapp", min_stock=5)
    add_movement(low, 4)
    enough = add_article("3D-0002", "Genug", min_stock=5)
    add_movement(enough, 5)
    add_article("3D-0003", "Ohne Minimum")

    html = client.get("/stock").text
    rows = re.findall(r"<tr\b.*?</tr>", html, re.DOTALL)
    marked = [row for row in rows if DE["stock.low"] in row]

    assert len(marked) == 1
    assert "3D-0001" in marked[0]


def test_deactivated_article_is_hidden_on_the_stock_page_but_shown_with_the_filter(
    client: AppClient,
) -> None:
    add_article(active=False)

    assert "3D-0001" not in client.get("/stock").text
    assert "3D-0001" in client.get("/stock?inactive=1").text


# --- Test cases 4-5: book by hand ------------------------------------------------------------


def test_withdrawal_lowers_the_stock(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    response = book(client, article_id, quantity="3", reason="Muster verschenkt")

    assert response.status_code == 303
    assert stock(article_id) == 7
    stored = movements()[-1]
    # A withdrawal is a correction with a minus; the reason says why.
    assert (stored.quantity, stored.kind, stored.reason) == (-3, "correction", "Muster verschenkt")
    assert stored.cancels_id is None
    assert stored.user_id is not None


@pytest.mark.parametrize(("typed", "result"), [("5", 15), ("+5", 15), ("-2", 8)])
def test_correction_changes_the_stock_in_either_direction(
    client: AppClient, typed: str, result: int
) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    response = book(client, article_id, type="correction", quantity=typed, reason="Inventur")

    assert response.status_code == 303
    assert stock(article_id) == result
    assert movements()[-1].kind == "correction"


def test_booking_date_is_today_by_default_and_can_be_chosen(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    book(client, article_id, quantity="1")
    book(client, article_id, quantity="1", date="2026-02-03")

    default, chosen = movements()[-2:]
    assert default.booked_on == date.today()
    assert chosen.booked_on == date(2026, 2, 3)


# --- Test cases 6-9: refused bookings --------------------------------------------------------


def test_booking_without_reason_is_refused(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    for reason in ("", "   "):
        response = book(client, article_id, reason=reason)
        assert response.status_code == 422
        assert field_error(response.text, "reason") == DE["stock.error.reason"]

    assert stock(article_id) == 10


@pytest.mark.parametrize("quantity", ["", "0", "abc", "1,5", "1.5", "2 Stück", "+0", "-0"])
def test_invalid_quantity_is_refused(client: AppClient, quantity: str) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    response = book(client, article_id, type="correction", quantity=quantity)

    assert response.status_code == 422
    assert field_error(response.text, "quantity") in (
        DE["stock.error.quantity"],
        DE["stock.error.zero"],
    )
    assert stock(article_id) == 10


def test_withdrawal_takes_a_positive_number_only(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    response = book(client, article_id, type="withdrawal", quantity="-3")

    assert response.status_code == 422
    assert field_error(response.text, "quantity") == DE["stock.error.quantity"]
    assert stock(article_id) == 10


@pytest.mark.parametrize("article", ["", "abc", "999"])
def test_unknown_article_is_refused(client: AppClient, article: str) -> None:
    form = {**booking(0), "article_id": article}
    response = client.post("/stock/new", data=form, follow_redirects=False)

    assert response.status_code == 422
    assert field_error(response.text, "article_id") == DE["stock.error.article"]
    assert movements() == []


@pytest.mark.parametrize("kind", ["", "sale", "production", "cancellation", "abc"])
def test_only_withdrawal_and_correction_can_be_booked_by_hand(client: AppClient, kind: str) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    response = book(client, article_id, type=kind)

    assert response.status_code == 422
    assert stock(article_id) == 10


def test_withdrawal_below_zero_is_refused(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 2)

    response = book(client, article_id, quantity="3")

    assert response.status_code == 422
    assert field_error(response.text, "quantity") == DE["stock.error.insufficient"]
    assert stock(article_id) == 2


def test_withdrawal_of_the_whole_stock_is_allowed(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 3)

    assert book(client, article_id, quantity="3").status_code == 303
    assert stock(article_id) == 0


def test_correction_below_zero_is_refused(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 2)

    response = book(client, article_id, type="correction", quantity="-3", reason="Inventur")

    assert response.status_code == 422
    assert field_error(response.text, "quantity") == DE["stock.error.insufficient"]
    assert stock(article_id) == 2


def test_nothing_can_be_booked_on_a_deactivated_article(client: AppClient) -> None:
    article_id = add_article(active=False)

    response = book(client, article_id, type="correction", quantity="5", reason="Inventur")

    assert response.status_code == 422
    assert field_error(response.text, "article_id") == DE["stock.error.inactive"]
    assert movements() == []


def test_booking_date_must_be_a_real_date_and_not_in_the_future(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    tomorrow = (date.today() + timedelta(days=2)).isoformat()

    for bad in (tomorrow, "morgen", "2026-13-40"):
        response = book(client, article_id, date=bad)
        assert response.status_code == 422
        assert field_error(response.text, "date") == DE["stock.error.date"]

    assert stock(article_id) == 10


def test_a_refused_form_keeps_what_was_typed(client: AppClient) -> None:
    article_id = add_article()

    html = book(client, article_id, quantity="abc", reason="Mein Grund").text

    assert "Mein Grund" in html


# --- Test cases 10-14: cancel instead of delete ----------------------------------------------


def cancel(client: AppClient, movement_id: int):
    return client.post(f"/stock/movements/{movement_id}/cancel", data={}, follow_redirects=False)


def test_cancelling_books_the_opposite_amount_and_restores_the_stock(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, quantity="3")
    original = movements()[-1]

    response = cancel(client, original.id)

    assert response.status_code == 303
    assert stock(article_id) == 10
    stored = movements()
    assert len(stored) == 3
    reversal = stored[-1]
    assert (reversal.quantity, reversal.kind, reversal.cancels_id) == (
        3,
        "cancellation",
        original.id,
    )
    assert reversal.article_id == article_id
    assert reversal.user_id is not None


def test_cancelling_a_plus_booking_takes_the_amount_away_again(client: AppClient) -> None:
    article_id = add_article()
    book(client, article_id, type="correction", quantity="5", reason="Inventur")
    original = movements()[-1]

    assert cancel(client, original.id).status_code == 303

    assert stock(article_id) == 0
    assert movements()[-1].quantity == -5


def test_original_stays_unchanged_and_is_marked_as_cancelled(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, quantity="3", reason="Muster verschenkt")
    original = movements()[-1]
    before = (original.quantity, original.kind, original.reason, original.booked_on)

    cancel(client, original.id)

    after = movements()[1]
    assert (after.quantity, after.kind, after.reason, after.booked_on) == before
    html = client.get("/stock/movements").text
    assert html.count("Muster verschenkt") == 1
    row = next(r for r in re.findall(r"<tr\b.*?</tr>", html, re.DOTALL) if "Muster" in r)
    assert DE["stock.cancelled"] in row
    # The cancelled booking offers no cancel button any more.
    assert f"/stock/movements/{original.id}/cancel" not in html


def test_there_is_no_way_to_delete_or_change_a_movement(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    movement_id = movements()[0].id

    for method, path in (
        ("delete", f"/stock/movements/{movement_id}"),
        ("post", f"/stock/movements/{movement_id}"),
        ("post", f"/stock/movements/{movement_id}/delete"),
        ("post", f"/stock/movements/{movement_id}/edit"),
        ("put", f"/stock/movements/{movement_id}"),
    ):
        assert client.request(method, path, data={}).status_code in (403, 404, 405), path
    assert len(movements()) == 1


def test_a_booking_can_only_be_cancelled_once(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, quantity="3")
    original = movements()[-1]
    cancel(client, original.id)

    response = cancel(client, original.id)

    assert response.status_code == 422
    assert DE["stock.error.cancelled_already"] in response.text
    assert len(movements()) == 3
    assert stock(article_id) == 10


def test_a_cancellation_cannot_be_cancelled(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, quantity="3")
    cancel(client, movements()[-1].id)
    reversal = movements()[-1]

    response = cancel(client, reversal.id)

    assert response.status_code == 422
    assert DE["stock.error.cancel_cancellation"] in response.text
    assert len(movements()) == 3


def test_cancelling_that_would_push_the_stock_below_zero_is_refused(client: AppClient) -> None:
    article_id = add_article()
    book(client, article_id, type="correction", quantity="5", reason="Zugang")
    original = movements()[-1]
    add_movement(article_id, -3, kind="sale", reason="Verkauf")

    response = cancel(client, original.id)

    assert response.status_code == 422
    assert DE["stock.error.insufficient"] in response.text
    assert stock(article_id) == 2
    assert len(movements()) == 2


@pytest.mark.parametrize("kind", ["production", "sale"])
def test_movements_of_production_and_sale_are_cancelled_at_their_source(
    client: AppClient, kind: str
) -> None:
    article_id = add_article()
    movement_id = add_movement(article_id, 4, kind=kind, reason="Bezug")

    response = cancel(client, movement_id)

    assert response.status_code == 422
    assert DE["stock.error.cancel_at_source"] in response.text
    assert stock(article_id) == 4
    assert f"/stock/movements/{movement_id}/cancel" not in client.get("/stock/movements").text


def test_cancelling_an_unknown_movement_gives_404(client: AppClient) -> None:
    assert cancel(client, 999).status_code == 404


def test_database_allows_a_movement_to_be_cancelled_only_once(client: AppClient) -> None:
    """Even if two cancels arrive at the same moment, the database refuses the second."""
    article_id = add_article()
    original = add_movement(article_id, 5)
    add_movement(article_id, -5, kind="cancellation", cancels_id=original)

    with pytest.raises(IntegrityError):
        add_movement(article_id, -5, kind="cancellation", cancels_id=original)


# --- Test case 15: list of movements ---------------------------------------------------------


def test_list_shows_date_article_amount_kind_reason_and_user_newest_first(
    client: AppClient,
) -> None:
    article_id = add_article()
    add_movement(article_id, 10, reason="Startbestand")
    book(client, article_id, quantity="3", reason="Muster verschenkt", date="2026-02-03")

    html = client.get("/stock/movements").text
    rows = re.findall(r"<tr\b.*?</tr>", html, re.DOTALL)
    newest = next(row for row in rows if "Muster verschenkt" in row)

    assert "03.02.2026" in newest
    assert "3D-0001" in newest
    assert "-3" in newest
    assert DE["stock.kind.correction"] in newest
    assert "werkstatt" in newest
    assert html.index("Muster verschenkt") < html.index("Startbestand")


def test_list_marks_the_cancellation_with_its_kind_and_the_booking_it_belongs_to(
    client: AppClient,
) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, quantity="3")
    cancel(client, movements()[-1].id)

    html = client.get("/stock/movements").text

    assert DE["stock.kind.cancellation"] in html


def test_list_can_be_filtered_by_article(client: AppClient) -> None:
    first = add_article()
    second = add_article("3D-0002", "Clip")
    add_movement(first, 1, reason="Erster Grund")
    add_movement(second, 1, reason="Zweiter Grund")

    html = client.get(f"/stock/movements?article={second}").text

    assert "Zweiter Grund" in html
    assert "Erster Grund" not in html
    assert client.get("/stock/movements?article=abc").status_code == 200
    assert "Erster Grund" in client.get("/stock/movements?article=abc").text


def test_empty_list_says_so(client: AppClient) -> None:
    assert DE["stock.movements.empty"] in client.get("/stock/movements").text


def test_article_stock_tile_counts_and_opens_the_movements_of_that_article(
    client: AppClient,
) -> None:
    first = add_article()
    second = add_article("3D-0002", "Clip")
    add_movement(first, 1, reason="Erster Grund")
    add_movement(first, 2, reason="Dritter Grund")
    add_movement(second, 1, reason="Zweiter Grund")

    detail = client.get(f"/articles/{first}").text
    section = client.get(f"/articles/{first}/stock").text

    tile = rf'href="/articles/{first}/stock"[^>]*>\s*<span class="tile-count">2<'
    assert re.search(tile, detail)
    assert "Erster Grund" in section
    assert "Dritter Grund" in section
    assert "Zweiter Grund" not in section


def test_article_stock_section_without_movements_says_so(client: AppClient) -> None:
    article_id = add_article()

    assert DE["article.section.empty"] in client.get(f"/articles/{article_id}/stock").text


def test_navigation_links_to_the_stock_page(client: AppClient) -> None:
    html = client.get("/").text

    assert re.search(r'<a href="/stock"[^>]*>' + DE["nav.stock"], html)
    assert client.get("/stock").status_code == 200
    assert client.get("/stock/movements").status_code == 200


# --- Test cases 16-18: login and roles -------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/stock"),
        ("get", "/stock/new"),
        ("post", "/stock/new"),
        ("get", "/stock/movements"),
        ("post", "/stock/movements/1/cancel"),
    ],
)
def test_without_login_every_page_redirects_to_login(
    client: AppClient, method: str, path: str
) -> None:
    with AppClient(create_app()) as visitor:
        response = visitor.request(method, path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_reader_may_look_but_not_book_or_cancel(client: AppClient, reader: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, quantity="1")
    booked = movements()[-1].id

    stock_page = reader.get("/stock")
    list_page = reader.get("/stock/movements")
    assert stock_page.status_code == 200
    assert list_page.status_code == 200
    assert 'href="/stock/new"' not in stock_page.text
    assert "/cancel" not in list_page.text

    assert reader.get("/stock/new").status_code == 403
    assert reader.post("/stock/new", data=booking(article_id)).status_code == 403
    assert reader.post(f"/stock/movements/{booked}/cancel", data={}).status_code == 403
    assert stock(article_id) == 9
    assert len(movements()) == 2


def test_employee_may_book_and_cancel(client: AppClient, employee: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    assert 'href="/stock/new"' in employee.get("/stock").text
    assert book(employee, article_id, quantity="2").status_code == 303
    booked = movements()[-1].id
    assert f"/stock/movements/{booked}/cancel" in employee.get("/stock/movements").text
    assert cancel(employee, booked).status_code == 303
    assert stock(article_id) == 10


def test_admin_may_book_and_cancel(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    assert client.get("/stock/new").status_code == 200
    assert book(client, article_id, quantity="2").status_code == 303
    assert cancel(client, movements()[-1].id).status_code == 303
    assert stock(article_id) == 10


# --- Test case 19: form token and escaping ---------------------------------------------------


def test_forms_carry_a_form_token(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, quantity="1")

    assert 'name="csrf_token"' in client.get("/stock/new").text
    assert 'name="csrf_token"' in client.get("/stock/movements").text


@pytest.mark.parametrize("path", ["/stock/new", "/stock/movements/1/cancel"])
def test_post_without_a_valid_form_token_is_refused(client: AppClient, path: str) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    client.csrf_token = None
    for data in ({}, {"csrf_token": "falsch"}):
        response = client.post(path, data={**booking(article_id), **data})
        assert response.status_code == 403

    assert len(movements()) == 1
    assert stock(article_id) == 10


def test_reason_is_escaped_in_the_list(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, reason="<script>alert(1)</script>")

    html = client.get("/stock/movements").text

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_reason_has_a_length_limit(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)

    response = book(client, article_id, reason="x" * 201)

    assert response.status_code == 422
    assert field_error(response.text, "reason") == DE["stock.error.reason_long"]
    assert len(movements()) == 1


# --- Test case 20: change log ----------------------------------------------------------------


def logged() -> list[tuple[str, str, int | None, list[dict[str, str]]]]:
    with db() as session:
        entries = session.query(ChangeLogEntry).order_by(ChangeLogEntry.id).all()
        return [
            (e.action, e.subject, e.user_id, json.loads(e.details))
            for e in entries
            if e.action.startswith("stock.")
        ]


def test_booking_and_cancelling_are_written_to_the_change_log(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, quantity="3", reason="Muster verschenkt")
    cancel(client, movements()[-1].id)

    with db() as session:
        admin_id = session.query(User).filter_by(username="werkstatt").one().id
    booked, cancelled = logged()
    assert booked[:3] == ("stock.booked", "3D-0001", admin_id)
    assert {"field": "quantity", "old": "", "new": "-3"} in booked[3]
    assert {"field": "reason", "old": "", "new": "Muster verschenkt"} in booked[3]
    assert cancelled[:3] == ("stock.cancelled", "3D-0001", admin_id)
    assert {"field": "quantity", "old": "", "new": "3"} in cancelled[3]


def test_refused_bookings_are_not_logged(client: AppClient) -> None:
    article_id = add_article()
    book(client, article_id, quantity="3")

    assert logged() == []


def test_change_log_page_shows_the_stock_actions(client: AppClient) -> None:
    article_id = add_article()
    add_movement(article_id, 10)
    book(client, article_id, quantity="3")

    assert DE["log.action.stock.booked"] in client.get("/settings/log").text


# --- Test case 21 (browser test): see tests/browser/test_stock.py -----------------------------

# --- Test case 22: migration -----------------------------------------------------------------


def test_stock_migration_on_empty_and_filled_database(tmp_path: Path) -> None:
    database = tmp_path / "stock.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0008")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO articles (number, number_key, name, active, created_at)"
            " VALUES ('3D-1', '3d-1', 'Erfunden', 1, '2026-01-01 00:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT number FROM articles").fetchall() == [("3D-1",)]
        assert connection.execute("SELECT COUNT(*) FROM stock_movements").fetchone() == (0,)
        connection.execute("PRAGMA foreign_keys = ON")
        insert = (
            "INSERT INTO stock_movements"
            " (article_id, booked_on, created_at, quantity, kind, reason)"
            " VALUES (?, '2026-01-02', '2026-01-02 10:00:00', ?, ?, 'x')"
        )
        connection.execute(insert, (1, 5, "correction"))
        # The database itself refuses unknown kinds, a missing article and a quantity of 0.
        for values in ((1, 5, "gift"), (99, 5, "correction"), (1, 0, "correction")):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, values)


def test_stock_migration_can_be_undone(tmp_path: Path) -> None:
    database = tmp_path / "stock_down.db"
    engine = make_engine(database)
    run_migrations(engine)

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0008")
    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM stock_movements").fetchone() == (0,)


# --- Texts from the translation files --------------------------------------------------------


def test_all_stock_texts_exist_in_both_languages() -> None:
    german = {key for key in DE if key.startswith(("stock", "log.action.stock"))}
    english = {key for key in EN if key.startswith(("stock", "log.action.stock"))}

    assert german
    assert german == english


def test_english_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_LANGUAGE", "en")
    with signed_in_client() as client:
        add_article(min_stock=5)

        html = client.get("/stock").text

    assert EN["stock.title"] in html
    assert EN["stock.low"] in html
