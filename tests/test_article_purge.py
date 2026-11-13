"""Deleting an article for good, admin only (issue #47, test cases 1-13).

All data is invented. The test case numbers refer to the comment on the issue.
"""

import json
from pathlib import Path

import pytest

from app import audit
from app.main import create_app
from app.models import Article, ChangeLogEntry, StockMovement
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_stock import add_article, add_movement, db, stock

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))

BUTTON = DE["article.purge.button"]


def purge(client: AppClient, article_id: int, number: str):
    return client.post(
        f"/articles/{article_id}/purge", data={"number": number}, follow_redirects=False
    )


def article_with_stock(number: str = "3D-0001") -> int:
    article_id = add_article(number, "Kabelhalter")
    booked = add_movement(article_id, 10)
    add_movement(article_id, -4)
    add_movement(article_id, -10, kind="cancellation", reason="", cancels_id=booked)
    return article_id


def article_numbers() -> list[str]:
    with db() as session:
        return [a.number for a in session.query(Article).order_by(Article.id)]


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


# --- Test cases 1-4: button and rights ------------------------------------------------------


def test_admin_sees_the_button_when_stock_movements_hang_on_the_article(
    client: AppClient,
) -> None:
    article_id = article_with_stock()

    assert BUTTON in client.get(f"/articles/{article_id}").text


def test_no_button_without_stock_movements(client: AppClient) -> None:
    article_id = add_article()

    assert BUTTON not in client.get(f"/articles/{article_id}").text
    assert client.get(f"/articles/{article_id}/purge").status_code == 404
    assert purge(client, article_id, "3D-0001").status_code == 404
    assert article_numbers() == ["3D-0001"]


def test_employee_and_reader_get_no_button_and_a_403(
    client: AppClient, employee: AppClient, reader: AppClient
) -> None:
    article_id = article_with_stock()

    for other in (employee, reader):
        assert BUTTON not in other.get(f"/articles/{article_id}").text
        assert other.get(f"/articles/{article_id}/purge").status_code == 403
        assert purge(other, article_id, "3D-0001").status_code == 403
    assert article_numbers() == ["3D-0001"]
    assert len(db().query(StockMovement).all()) == 3


def test_login_is_required(client: AppClient) -> None:
    article_id = article_with_stock()

    with AppClient(create_app()) as anonymous:
        response = anonymous.get(f"/articles/{article_id}/purge", follow_redirects=False)
        assert response.status_code in (302, 303)
        assert "/login" in response.headers["location"]
        assert purge(anonymous, article_id, "3D-0001").status_code in (302, 303, 403)
    assert article_numbers() == ["3D-0001"]


# --- Test cases 5-6: warning and confirmation -----------------------------------------------


def test_warning_names_the_counts_and_asks_for_the_number(client: AppClient) -> None:
    article_id = article_with_stock()

    html = client.get(f"/articles/{article_id}/purge").text

    assert DE["article.purge.irreversible"] in html
    assert "3D-0001" in html
    for key, count in (
        ("calculations", 0),
        ("productions", 0),
        ("stock_movements", 3),
        ("sales", 0),
    ):
        assert f"{DE['article.purge.count.' + key]}: {count}" in html
    assert 'name="number"' in html
    assert 'name="csrf_token"' in html


@pytest.mark.parametrize("typed", ["", "3D-0002", "3d-0001", " "])
def test_wrong_or_empty_number_deletes_nothing(client: AppClient, typed: str) -> None:
    article_id = article_with_stock()

    response = purge(client, article_id, typed)

    assert response.status_code == 422
    assert DE["article.purge.mismatch"] in response.text
    assert article_numbers() == ["3D-0001"]
    assert len(db().query(StockMovement).all()) == 3


# --- Test cases 7-10: the deletion ----------------------------------------------------------


def test_right_number_deletes_the_article_and_all_its_movements(client: AppClient) -> None:
    article_id = article_with_stock()

    response = purge(client, article_id, "3D-0001")

    assert response.status_code == 303
    assert response.headers["location"] == "/articles?purged=1"
    assert article_numbers() == []
    assert db().query(StockMovement).count() == 0
    assert client.get(f"/articles/{article_id}").status_code == 404
    page = client.get(response.headers["location"]).text
    assert DE["article.purge.done"] in page


def test_other_articles_stay_untouched(client: AppClient) -> None:
    gone = article_with_stock()
    other = add_article("3D-0002", "Clip")
    add_movement(other, 7)

    purge(client, gone, "3D-0001")

    assert article_numbers() == ["3D-0002"]
    assert stock(other) == 7
    assert db().query(StockMovement).count() == 1


def test_all_or_nothing(client: AppClient, monkeypatch: pytest.MonkeyPatch) -> None:
    article_id = article_with_stock()

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("log failed")

    monkeypatch.setattr(audit, "record_change", broken)

    with pytest.raises(RuntimeError):
        purge(client, article_id, "3D-0001")

    assert article_numbers() == ["3D-0001"]
    assert db().query(StockMovement).count() == 3


def test_the_deleted_article_is_gone_from_lists_and_dashboard(client: AppClient) -> None:
    article_id = add_article("3D-0001", "Kabelhalter", min_stock=50)
    add_movement(article_id, 1)
    assert "3D-0001" in client.get("/").text

    purge(client, article_id, "3D-0001")

    for path in ("/articles?inactive=1", "/stock", "/stock/movements", "/"):
        assert "3D-0001" not in client.get(path).text


# --- Test case 11: change log ---------------------------------------------------------------


def test_the_change_log_says_who_which_article_and_how_many_records(client: AppClient) -> None:
    article_id = article_with_stock()

    purge(client, article_id, "3D-0001")

    with db() as session:
        entry = session.query(ChangeLogEntry).filter_by(action="article.purged").one()
    assert entry.subject == "3D-0001 · Kabelhalter"
    assert entry.user_id is not None
    assert entry.created_at is not None
    details = {item["field"]: item["old"] for item in json.loads(entry.details)}
    assert details == {
        "calculations": "0",
        "productions": "0",
        "stock_movements": "3",
        "sales": "0",
    }
    html = client.get("/settings/log").text
    assert DE["log.action.article.purged"] in html
    assert f"{DE['article.purge.count.stock_movements']}: 3 → 0" in html


def test_a_wrong_number_writes_no_log_entry(client: AppClient) -> None:
    article_id = article_with_stock()

    purge(client, article_id, "nope")

    with db() as session:
        assert session.query(ChangeLogEntry).filter_by(action="article.purged").count() == 0


# --- Test case 12: CSRF ---------------------------------------------------------------------


def test_without_a_form_token_nothing_is_deleted(client: AppClient) -> None:
    article_id = article_with_stock()
    client.csrf_token = None

    response = purge(client, article_id, "3D-0001")

    assert response.status_code == 403
    assert article_numbers() == ["3D-0001"]


# --- Test case 13: translation and escaping -------------------------------------------------


def test_texts_exist_in_both_languages() -> None:
    keys = [key for key in DE if key.startswith(("article.purge", "log.action.article.purged"))]
    assert keys
    assert all(key in EN for key in keys)


def test_names_are_escaped_on_the_warning_page(client: AppClient) -> None:
    article_id = add_article("3D-0001", "<script>alert(1)</script>")
    add_movement(article_id, 1)

    html = client.get(f"/articles/{article_id}/purge").text

    assert "<script>alert(1)" not in html
