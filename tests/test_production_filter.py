"""Production list: column "Printer" and filter bar (issue #259). All data is invented.
The numbers refer to the test cases in the comment on the issue."""

import json
import re
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select

from app import production
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import ProductionPrint
from app.production import PrintInput, ResultInput
from tests.auth import AppClient, signed_in_client
from tests.machine import add_printer
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_booking import form, productions, send
from tests.test_production_machine import ARCHIVES_1H
from tests.test_production_page import prepared
from tests.test_stock import add_article, db

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal
LIST = "/production/list"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def book(
    article_id: int,
    day: date,
    *printers: str | None,
    archive_start: int = 1,
) -> int:
    """Books a production with one print per printer name (None = no printer known)."""
    prints = [
        PrintInput(archive_start + index, D(1), D(0), D(0), printer_name=name)
        for index, name in enumerate(printers)
    ]
    with db() as session, session.begin():
        production_id, errors = production.book(
            session, None, day, "", prints, ResultInput(article_id, 2)
        )
    assert not errors
    return production_id


def cancel(production_id: int) -> None:
    with db() as session, session.begin():
        assert production.cancel(session, None, production_id) is None


def rows(html: str) -> list[str]:
    return re.findall(r'<tr class="production-row.*?</tr>', html, re.S)


def cell(row: str, column: str) -> str:
    match = re.search(rf'<td class="[^"]*\bcol-{column}\b[^"]*">(.*?)</td>', row, re.S)
    assert match, column
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def ids(client: AppClient, query: str = "") -> list[int]:
    html = client.get(f"{LIST}?{query}").text
    return [int(re.search(r'data-id="(\d+)"', row).group(1)) for row in rows(html)]


@pytest.fixture
def three(client: AppClient) -> dict[str, int]:
    """Newest first by date: c (Drucker Zeta, 10.11., article B), b (Alpha + Zeta, 05.11.,
    article A, cancelled), a (Drucker Alpha, 01.11., article A)."""
    first, second = (
        add_article(number="A-1", name="Alpha-Artikel"),
        add_article(number="B-2", name="Beta-Artikel"),
    )
    a = book(first, date(2026, 11, 1), "Drucker Alpha", archive_start=1)
    b = book(first, date(2026, 11, 5), "Drucker Alpha", "Drucker Zeta", archive_start=10)
    c = book(second, date(2026, 11, 10), "Drucker Zeta", archive_start=20)
    cancel(b)
    return {"a": a, "b": b, "c": c, "article_a": first, "article_b": second}


# --- The printer is stored automatically when booking -------------------------------------------


def test_4_booking_stores_the_printer_of_the_print_by_itself(client: AppClient) -> None:
    prepared(client, ARCHIVES_1H)
    add_printer("12.00", name="Drucker Alpha")  # linked to Bambuddy printer 1 like the prints
    article_id = add_article()

    send(client, form([6], (article_id, "5")))

    (item,) = productions()
    assert item.prints[0].printer_name == "Drucker Alpha"


def test_5_old_productions_have_no_printer_and_show_a_dash(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, date(2026, 11, 1), None)

    (row,) = rows(client.get(LIST).text)

    assert cell(row, "printer") == DE["table.empty_value"]


def test_5_migration_adds_an_empty_printer_field_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "printer.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0035")
    run_migrations(engine)
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(production_prints)")}
        assert "printer_name" in columns
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0035")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(production_prints)")}
        assert "printer_name" not in columns
    run_migrations(engine)


# --- The column ----------------------------------------------------------------------------------


def test_1_the_printer_column_sits_next_to_the_date(client: AppClient, three) -> None:
    html = client.get(LIST).text
    heads = re.findall(r"<th\b.*?</th>", html, re.S)

    assert DE["production.col.date"] in heads[0]
    assert DE["production.col.printer"] in heads[1]
    row = next(r for r in rows(html) if f'data-id="{three["a"]}"' in r)
    assert cell(row, "printer") == "Drucker Alpha"


def test_2_several_printers_of_one_production_are_all_listed(client: AppClient, three) -> None:
    row = next(r for r in rows(client.get(LIST).text) if f'data-id="{three["b"]}"' in r)

    assert cell(row, "printer") == "Drucker Alpha, Drucker Zeta"


def test_3_the_same_printer_twice_is_listed_once(client: AppClient) -> None:
    article_id = add_article()
    book(article_id, date(2026, 11, 1), "Drucker Alpha", "Drucker Alpha", archive_start=1)

    (row,) = rows(client.get(LIST).text)

    assert cell(row, "printer") == "Drucker Alpha"


def test_6_the_printer_column_sorts_both_ways_and_keeps_filters(client: AppClient, three) -> None:
    assert ids(client, "sort=printer&dir=asc") == [three["a"], three["b"], three["c"]]
    assert ids(client, "sort=printer&dir=desc") == [three["c"], three["b"], three["a"]]
    html = client.get(f"{LIST}?status=booked").text
    assert "status=booked" in re.findall(r'href="([^"]*sort=printer[^"]*)"', html)[0]


# --- The filters ---------------------------------------------------------------------------------


def test_7_the_period_includes_both_limits_and_ignores_bad_dates(client: AppClient, three) -> None:
    assert ids(client, "from=2026-11-05&to=2026-11-10") == [three["c"], three["b"]]
    assert ids(client, "from=2026-11-06") == [three["c"]]
    assert ids(client, "to=2026-11-01") == [three["a"]]
    assert ids(client, "from=kaputt&to=2026-13-45") == [three["c"], three["b"], three["a"]]


def test_8_the_article_filter_includes_the_article_of_any_result(client: AppClient, three) -> None:
    assert ids(client, f"article={three['article_a']}") == [three["b"], three["a"]]
    assert ids(client, f"article={three['article_b']}") == [three["c"]]
    assert ids(client, "article=999999") == [three["c"], three["b"], three["a"]]


def test_9_the_printer_filter_matches_when_any_print_has_that_printer(
    client: AppClient, three
) -> None:
    assert ids(client, "printer=Drucker+Alpha") == [three["b"], three["a"]]
    assert ids(client, "printer=Drucker+Zeta") == [three["c"], three["b"]]
    assert ids(client, "printer=Unbekannt") == [three["c"], three["b"], three["a"]]


def test_10_the_status_filter(client: AppClient, three) -> None:
    assert ids(client, "status=booked") == [three["c"], three["a"]]
    assert ids(client, "status=cancelled") == [three["b"]]
    assert ids(client, "status=all") == [three["c"], three["b"], three["a"]]
    assert ids(client, "status=quatsch") == [three["c"], three["b"], three["a"]]


def test_11_filters_combine_with_chips_reset_and_sort(client: AppClient, three) -> None:
    html = client.get(
        f"{LIST}?printer=Drucker+Alpha&status=booked&sort=cost&dir=asc&from=2026-11-01"
    ).text

    assert [int(m) for m in re.findall(r'data-id="(\d+)"', html)] == [three["a"]]
    chips = re.findall(r'<li class="filter-chip">(.*?)</li>', html, re.S)
    assert len(chips) == 3
    assert any("Drucker Alpha" in chip for chip in chips)
    assert any(DE["production.filter.status.booked"] in chip for chip in chips)
    remove = re.findall(r'class="filter-chip">[^<]*<a href="([^"]*)"', html)
    assert all("sort=cost" in link and "dir=asc" in link for link in remove)
    assert 'href="/production/list?sort=cost&amp;dir=asc"' in html or (
        'href="/production/list?sort=cost&dir=asc"' in html
    )


def test_12_hints_for_no_hits_and_for_no_production_at_all(client: AppClient, three) -> None:
    html = client.get(f"{LIST}?printer=Drucker+Zeta&to=2026-11-02").text
    assert DE["filter.no_results"] in html
    assert DE["production.list.empty"] not in html


def test_12_without_any_production_the_old_hint_stays(client: AppClient) -> None:
    html = client.get(LIST).text

    assert DE["production.list.empty"] in html
    assert DE["filter.no_results"] not in html


def test_13_the_page_still_needs_a_login() -> None:
    with AppClient(create_app()) as anonymous:
        response = anonymous.get(f"{LIST}?printer=x", follow_redirects=False)
    assert response.status_code in (303, 401, 403)


def test_14_texts_exist_in_german_and_english() -> None:
    for key in (
        "production.col.printer",
        "production.filter.article",
        "production.filter.article.all",
        "production.filter.from",
        "production.filter.to",
        "production.filter.printer",
        "production.filter.printer.all",
        "production.filter.state",
        "production.filter.status.all",
        "production.filter.status.booked",
        "production.filter.status.cancelled",
    ):
        assert DE.get(key), key
        assert EN.get(key), key


def test_printer_name_is_stored_as_a_value() -> None:
    article_id = add_article()
    book(article_id, date(2026, 11, 1), "Drucker Alpha")
    with db() as session:
        assert session.scalars(select(ProductionPrint.printer_name)).one() == "Drucker Alpha"
