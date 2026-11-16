"""Article detail: productions and cost per piece (issue #120). All data is invented. The test
case numbers refer to the comment on the issue; case 10 (phone view) is a browser test.
"""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import production
from app.main import create_app
from app.models import BambuddyConnection
from app.production import PrintInput, ResultInput
from tests.auth import READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_stock import add_article, db

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal


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
    archive_ids: tuple[int, ...] = (1,),
    failed_pieces: int = 0,
    day: int = 15,
) -> int:
    """Books a production whose prints cost `total` together (all on the material)."""
    share = D(total) / len(archive_ids)
    prints = [PrintInput(a, share, D(0), D(0)) for a in archive_ids]
    with db() as session, session.begin():
        production_id, errors = production.book(
            session,
            None,
            date(2026, 11, day),
            "",
            prints,
            ResultInput(article_id, quantity),
            failed_pieces=failed_pieces,
        )
    assert not errors
    return production_id


def cancel(production_id: int) -> None:
    with db() as session, session.begin():
        assert production.cancel(session, None, production_id) is None


def set_address(url: str) -> None:
    with db() as session, session.begin():
        session.get(BambuddyConnection, 1).url = url


def table_rows(html: str) -> list[str]:
    body = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL)
    assert body
    return re.findall(r"<tr[ >].*?</tr>", body.group(1), re.DOTALL)


def text(fragment: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


# --- Case 1: counter ----------


def test_tile_counts_all_productions_of_the_article_including_cancelled(client: AppClient) -> None:
    article_id = add_article("A-1")
    book(article_id, 10, "12.00", (1,))
    cancel(book(article_id, 5, "5.00", (2,)))
    book(add_article("A-2"), 1, "1.00", (3,))

    html = client.get(f"/articles/{article_id}").text

    tile = re.search(
        rf'<a href="/articles/{article_id}/productions" class="tile">(.*?)</a>', html, re.DOTALL
    )
    assert tile and text(tile.group(1)) == f"2 {DE['article.tile.productions']}"


# --- Cases 2, 5, 7: list ----------


def test_list_shows_one_row_per_production_with_cost_per_piece(client: AppClient) -> None:
    article_id = add_article("A-1")
    production_id = book(article_id, 10, "12.00", (1,), failed_pieces=2, day=14)
    book(add_article("A-2"), 3, "9.00", (2,))

    page = client.get(f"/articles/{article_id}/productions")

    assert page.status_code == 200
    rows = table_rows(page.text)
    assert len(rows) == 1
    row = text(rows[0])
    assert "14.11.2026" in row
    assert "12,00" in row and "1,20" in row
    assert DE["production.state.active"] in row
    assert f'href="/production/{production_id}"' in rows[0]
    for head in ("production.col.date", "production.col.quantity", "production.col.cost"):
        assert DE[head] in page.text
    assert DE["production.col.per_piece"] in page.text
    assert DE["production.detail.failed_pieces"] in page.text
    cells = [text(c) for c in re.findall(r"<td.*?</td>", rows[0], re.DOTALL)]
    assert "2" in cells


# --- Cases 3, 4: cancelled and totals ----------


def test_cancelled_is_marked_and_left_out_of_the_totals(client: AppClient) -> None:
    article_id = add_article("A-1")
    book(article_id, 10, "12.00", (1,), day=10)
    book(article_id, 20, "18.00", (2,), day=11)
    cancel(book(article_id, 50, "99.00", (3,), day=12))

    page = client.get(f"/articles/{article_id}/productions")

    rows = table_rows(page.text)
    assert len(rows) == 3
    assert sum(DE["production.state.cancelled"] in text(r) for r in rows) == 1
    foot = re.search(r"<tfoot>(.*?)</tfoot>", page.text, re.DOTALL)
    assert foot
    summary = text(foot.group(1))
    assert "30" in summary and "30,00" in summary and "1,00" in summary
    assert "99" not in summary and "50" not in summary


def test_no_totals_when_everything_is_cancelled(client: AppClient) -> None:
    article_id = add_article("A-1")
    cancel(book(article_id, 10, "12.00", (1,)))

    page = client.get(f"/articles/{article_id}/productions")

    assert len(table_rows(page.text)) == 1
    assert "<tfoot>" not in page.text


# --- Case 6: Bambuddy links ----------


def test_print_numbers_link_to_bambuddy_when_an_address_is_set(client: AppClient) -> None:
    article_id = add_article("A-1")
    book(article_id, 10, "12.00", (41, 42))
    set_address("http://bambuddy.example.com:8000")

    html = client.get(f"/articles/{article_id}/productions").text

    for number in (41, 42):
        link = re.search(rf'<a href="([^"]+)"[^>]*>\s*#{number}\s*</a>', html)
        assert link and link.group(1).startswith("http://bambuddy.example.com:8000/")
    assert 'target="_blank"' in html and "noopener" in html


def test_print_numbers_are_plain_text_without_an_address(client: AppClient) -> None:
    article_id = add_article("A-1")
    book(article_id, 10, "12.00", (41, 42))

    html = client.get(f"/articles/{article_id}/productions").text

    assert "#41" in html and "#42" in html
    assert '<a href="http' not in html
    assert 'target="_blank"' not in html


def test_the_address_is_escaped(client: AppClient) -> None:
    article_id = add_article("A-1")
    book(article_id, 1, "1.00", (7,))
    set_address('http://example.com/"><script>x</script>')

    html = client.get(f"/articles/{article_id}/productions").text

    assert "<script>x</script>" not in html


# --- Case 8: empty ----------


def test_empty_list_shows_a_hint_and_no_totals(client: AppClient) -> None:
    article_id = add_article("A-1")

    page = client.get(f"/articles/{article_id}/productions")

    assert page.status_code == 200
    assert DE["article.productions.empty"] in page.text
    assert "<tfoot>" not in page.text and "<table" not in page.text


# --- Case 9: rights ----------


def test_login_is_required() -> None:
    article_id = add_article("A-1")
    add_user(READER_NAME, "read_only")
    with AppClient(create_app()) as anonymous:
        response = anonymous.get(f"/articles/{article_id}/productions", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert "/login" in response.headers["location"]


def test_read_only_user_may_look(client: AppClient) -> None:
    article_id = add_article("A-1")
    book(article_id, 10, "12.00", (1,))
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        assert reader.get(f"/articles/{article_id}/productions").status_code == 200


def test_unknown_article_is_404(client: AppClient) -> None:
    assert client.get("/articles/9999/productions").status_code == 404


def test_texts_exist_in_both_languages() -> None:
    for key in ("article.productions.empty", "article.productions.totals"):
        assert DE[key] and EN[key]
