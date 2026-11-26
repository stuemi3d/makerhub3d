"""Page Production: column "Printer" in the views open, assigned and ignored (issue #275).
All data is invented; Bambuddy answers come from a mock. The numbers refer to the test cases
in the comment on the issue."""

import re
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app import production
from app.models import Device
from app.production import PrintInput, ResultInput
from tests.auth import AppClient, signed_in_client
from tests.machine import add_dryer, add_printer
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_page import ignore, names, prepared, raw, rows
from tests.test_stock import add_article, db

D = Decimal
DASH = "—"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def book(archive_id: int, printer_name: str | None) -> None:
    article_id = add_article(f"3D-{archive_id:04d}")
    with db() as session, session.begin():
        _, errors = production.book(
            session,
            None,
            date(2026, 11, 15),
            "",
            [PrintInput(archive_id, D(1), D(0), D(0), printer_name=printer_name)],
            ResultInput(article_id, 1),
        )
    assert not errors


def printer_cell(row: str) -> str:
    match = re.search(r'<td class="col-printer">(.*?)</td>', row, re.S)
    assert match
    return match.group(1).strip()


def printers(client: AppClient, query: str = "") -> dict[str, str]:
    """Print name -> shown printer of the page."""
    html = client.get(f"/production{query}").text
    return dict(zip(names(html), (printer_cell(row) for row in rows(html)), strict=True))


# --- Cases 1-4 and 8: names over the linked device ---------------------------------------------


def test_an_open_print_shows_the_linked_device(client: AppClient) -> None:
    add_printer("1", bambuddy_printer_id=1, name="Drucker Links")
    add_printer("1", bambuddy_printer_id=2, name="Drucker Rechts")
    prepared(client, [raw(2, name="Links", printer_id=1), raw(1, name="Rechts", printer_id=2)])
    assert printers(client) == {"Links": "Drucker Links", "Rechts": "Drucker Rechts"}


def test_without_a_linked_device_or_printer_id_a_dash_is_shown(client: AppClient) -> None:
    add_printer("1", bambuddy_printer_id=1, name="Drucker Links")
    prepared(
        client,
        [raw(2, name="Fremd", printer_id=7), raw(1, name="Ohne", printer_id=None)],
    )
    assert printers(client) == {"Fremd": DASH, "Ohne": DASH}


def test_an_ignored_print_shows_the_linked_device(client: AppClient) -> None:
    add_printer("1", bambuddy_printer_id=1, name="Drucker Links")
    prepared(client, [raw(1, name="Weg", printer_id=1)])
    assert ignore(client, 1).status_code == 303
    assert printers(client, "?view=ignored") == {"Weg": "Drucker Links"}


def test_a_switched_off_printer_still_names_its_prints(client: AppClient) -> None:
    add_printer("1", bambuddy_printer_id=1, name="Alter Drucker", active=False)
    prepared(client, [raw(1, name="Alt", printer_id=1)])
    assert printers(client) == {"Alt": "Alter Drucker"}


def test_a_dryer_is_never_shown_as_printer(client: AppClient) -> None:
    dryer = add_dryer("10", name="Trockner")
    with db() as session, session.begin():
        session.get(Device, dryer).bambuddy_printer_id = 1
    prepared(client, [raw(1, name="Druck", printer_id=1)])
    assert printers(client) == {"Druck": DASH}


# --- Cases 5-6: assigned prints ----------------------------------------------------------------


def test_the_stored_printer_of_the_production_comes_first(client: AppClient) -> None:
    add_printer("1", bambuddy_printer_id=1, name="Drucker Links")
    prepared(client, [raw(1, name="Gebucht", printer_id=1)])
    book(1, "Drucker Rechts")
    assert printers(client, "?view=assigned") == {"Gebucht": "Drucker Rechts"}


def test_older_productions_without_a_stored_printer_use_the_bambuddy_id(
    client: AppClient,
) -> None:
    add_printer("1", bambuddy_printer_id=1, name="Drucker Links")
    prepared(client, [raw(2, name="Alt", printer_id=1), raw(1, name="Unbekannt", printer_id=9)])
    book(2, None)
    book(1, None)
    assert printers(client, "?view=assigned") == {"Alt": "Drucker Links", "Unbekannt": DASH}


def test_a_cancelled_production_does_not_lend_its_printer(client: AppClient) -> None:
    add_printer("1", bambuddy_printer_id=1, name="Drucker Links")
    prepared(client, [raw(1, name="Wieder offen", printer_id=1)])
    book(1, "Drucker Rechts")
    with db() as session, session.begin():
        production_id = session.scalar(select(func.max(production.Production.id)))
        assert production.cancel(session, None, production_id) is None
    assert printers(client) == {"Wieder offen": "Drucker Links"}


# --- Case 7: renaming shows at once ------------------------------------------------------------


def test_renaming_a_device_shows_the_new_name_at_once(client: AppClient) -> None:
    device = add_printer("1", bambuddy_printer_id=1, name="Drucker Links")
    prepared(client, [raw(1, name="Druck", printer_id=1)])
    assert printers(client) == {"Druck": "Drucker Links"}
    with db() as session, session.begin():
        session.get(Device, device).name = "Drucker Mitte"
    assert printers(client) == {"Druck": "Drucker Mitte"}


# --- Cases 9-10: sorting -----------------------------------------------------------------------


SORTABLE = [
    raw(3, "2026-11-15T10:00:00", name="Bei Berta", printer_id=2),
    raw(2, "2026-11-14T10:00:00", name="Ohne Drucker", printer_id=None),
    raw(1, "2026-11-13T10:00:00", name="Bei Anton", printer_id=1),
]


def test_the_printer_column_sorts_both_ways_with_dashes_first(client: AppClient) -> None:
    add_printer("1", bambuddy_printer_id=1, name="anton")
    add_printer("1", bambuddy_printer_id=2, name="Berta")
    prepared(client, SORTABLE)
    up = names(client.get("/production?sort=printer&dir=asc").text)
    assert up == ["Ohne Drucker", "Bei Anton", "Bei Berta"]
    down = names(client.get("/production?sort=printer&dir=desc").text)
    assert down == ["Bei Berta", "Bei Anton", "Ohne Drucker"]


def test_the_sort_links_keep_the_view(client: AppClient) -> None:
    add_printer("1", bambuddy_printer_id=1, name="Anton")
    prepared(client, SORTABLE)
    for target in (1, 2, 3):
        assert ignore(client, target).status_code == 303
    html = client.get("/production?view=ignored&sort=printer&dir=asc").text
    # Equal values (both without a printer) keep the order newest first
    assert names(html) == ["Bei Berta", "Ohne Drucker", "Bei Anton"]
    head = re.search(r"<thead>.*?</thead>", html, re.S).group(0)
    link = re.search(r'href="(/production\?[^"]*sort=printer[^"]*)"', head).group(1)
    assert "view=ignored" in link
    assert "dir=desc" in link


# --- Cases 11-12: placement and dashboard ------------------------------------------------------


@pytest.mark.parametrize("view", ["open", "assigned", "ignored"])
def test_the_column_sits_next_to_the_date_in_every_view(client: AppClient, view: str) -> None:
    prepared(client, [raw(1, name="Druck")])
    if view == "assigned":
        book(1, None)
    elif view == "ignored":
        assert ignore(client, 1).status_code == 303
    html = client.get(f"/production?view={view}").text
    head = re.search(r"<thead>.*?</thead>", html, re.S).group(0)
    titles = [
        re.sub(r"<[^>]+>|[▲▼↕]", "", cell).strip()
        for cell in re.findall(r"<th.*?</th>", head, re.S)
    ]
    date_at = titles.index("Datum")
    assert titles[date_at + 1] == "Drucker"
    assert printer_cell(rows(html)[0]) == DASH


def test_the_dashboard_gets_no_printer_column(client: AppClient) -> None:
    add_printer("1", bambuddy_printer_id=1, name="Drucker Links")
    prepared(client, [raw(1, name="Druck", printer_id=1)])
    html = client.get("/").text
    block = re.search(r'id="open-prints".*?</article>', html, re.S).group(0)
    assert "Drucker Links" not in block
    assert "col-printer" not in block
