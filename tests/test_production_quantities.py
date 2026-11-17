"""Production: print time, filament, electricity and failed pieces (issue #148). All data is
invented; Bambuddy answers come from a mock. The test case numbers refer to the comment on
the issue.
"""

import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest
from alembic import command
from sqlalchemy import select

from app.bambuddy import BambuddyClient
from app.db import alembic_config, make_engine, run_migrations
from app.models import ProductionPrint, StockMovement
from tests.auth import AppClient
from tests.machine import add_printer
from tests.test_bambuddy_client import BASE, KEY, fixture
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_booking import field_error, form, productions, send
from tests.test_production_page import prepared, raw, serve
from tests.test_stock import add_article, db, stock

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal

# Print 6: 2 h 30 min, 40 g, 0.3 kWh, black PLA. Print 7: 50 min, 15 g, 0.5 kWh, PETG in two
# colours. Print 8: nothing known. Print 9: hostile data in the filament fields.
ARCHIVES = [
    raw(
        9,
        "2026-11-15T12:00:00",
        name="Druck Boese",
        filament_type="PLA" * 40,
        filament_color="red;background:url(//example.com/x),#GGGGGG,#12AB34",
    ),
    raw(
        8,
        "2026-11-15T11:00:00",
        name="Druck leer",
        seconds=None,
        filament_used_grams=None,
        energy_kwh=None,
        filament_type=None,
        filament_color=None,
    ),
    raw(
        7,
        "2026-11-15T10:00:00",
        name="Druck B",
        seconds=3000,
        filament_used_grams=15,
        energy_kwh=0.5,
        filament_type="PETG",
        filament_color="#FFFFFF,#BB3D43",
    ),
    raw(
        6,
        "2026-11-15T09:00:00",
        name="Druck A",
        seconds=9000,
        filament_used_grams=40,
        energy_kwh=0.3,
        filament_type="PLA",
        filament_color="#000000",
    ),
]


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def prepared_client(client: AppClient) -> AppClient:
    prepared(client, ARCHIVES)
    add_printer("1.00")
    return client


def stored_prints() -> dict[int, ProductionPrint]:
    with db() as session:
        found = list(session.scalars(select(ProductionPrint)))
        session.expunge_all()
    return {p.archive_id: p for p in found}


def book(client: AppClient, ids: list[int], **extra: object) -> None:
    assert send(client, form(ids, (add_article(), "3"), **extra)).status_code == 303


# --- Case 1: time, grams and kWh are stored ----------------------------------------------------


def test_booking_stores_time_grams_and_kwh_of_the_print(prepared_client: AppClient) -> None:
    book(prepared_client, [6])

    item = stored_prints()[6]
    assert item.print_seconds == 9000
    assert item.filament_grams == D("40")
    assert item.energy_kwh == D("0.3")


# --- Case 2: sums and single values on the detail page -----------------------------------------


def test_the_detail_page_shows_single_values_and_sums(prepared_client: AppClient) -> None:
    book(prepared_client, [6, 7])

    html = prepared_client.get("/production/1").text

    rows = re.findall(r'<tr class="detail-print.*?</tr>', html, re.S)
    assert len(rows) == 2
    assert "2 h 30 min" in rows[0] and "40" in rows[0] and "0,3" in rows[0]
    assert "50 min" in rows[1] and "15" in rows[1] and "0,5" in rows[1]
    (totals,) = re.findall(r'<p class="detail-totals">.*?</p>', html, re.S)
    assert "3 h 20 min" in totals and "55" in totals and "0,8" in totals
    for key in ("production.col.duration", "production.col.grams", "production.col.kwh"):
        assert DE[key] in html


# --- Case 3: frozen when Bambuddy changes ------------------------------------------------------


def test_stored_values_stay_when_bambuddy_changes_the_print(prepared_client: AppClient) -> None:
    book(prepared_client, [6])
    changed = [dict(a) for a in ARCHIVES]
    for archive in changed:
        archive.update(
            actual_time_seconds=60,
            print_time_seconds=60,
            filament_used_grams=1,
            energy_kwh=0.01,
            filament_type="ABS",
            filament_color="#123456",
        )
    serve(prepared_client, changed)

    item = stored_prints()[6]
    html = prepared_client.get("/production/1").text

    assert (item.print_seconds, item.filament_grams, item.energy_kwh) == (9000, D("40"), D("0.3"))
    assert (item.filament_type, item.filament_colors) == ("PLA", "#000000")
    assert "2 h 30 min" in html and "ABS" not in html and "#123456" not in html


# --- Case 4: missing values stay empty ---------------------------------------------------------


def test_missing_values_stay_empty_and_booking_still_works(prepared_client: AppClient) -> None:
    book(prepared_client, [8])

    item = stored_prints()[8]
    assert item.print_seconds is None
    assert item.filament_grams is None and item.energy_kwh is None
    assert item.filament_type is None and item.filament_colors is None
    html = prepared_client.get("/production/1").text
    row = re.findall(r'<tr class="detail-print.*?</tr>', html, re.S)[0]
    assert row.count(DE["table.empty_value"]) >= 4
    assert " 0 g" not in row and "0 kWh" not in row
    (totals,) = re.findall(r'<p class="detail-totals">.*?</p>', html, re.S)
    assert DE["table.empty_value"] in totals


def test_sums_ignore_missing_values(prepared_client: AppClient) -> None:
    book(prepared_client, [6, 8])

    html = prepared_client.get("/production/1").text

    (totals,) = re.findall(r'<p class="detail-totals">.*?</p>', html, re.S)
    assert "2 h 30 min" in totals and "40" in totals


# --- Case 5: failed pieces ---------------------------------------------------------------------


def test_failed_pieces_are_stored_and_change_neither_stock_nor_cost(
    prepared_client: AppClient,
) -> None:
    article_id = add_article("A-2")
    response = send(prepared_client, form([6], (article_id, "3"), failed_pieces="2"))

    assert response.status_code == 303
    (item,) = productions()
    assert item.failed_pieces == 2
    assert item.results[0].quantity == 3
    assert stock(article_id) == 3
    # All costs of the production still go to the good pieces
    assert item.results[0].allocated_cost == item.total_cost
    assert DE["production.detail.failed_pieces"] in prepared_client.get("/production/1").text
    # Empty means none
    other_id = add_article("A-3")
    send(prepared_client, form([7], (other_id, "3"), failed_pieces=""))
    assert productions()[1].failed_pieces == 0


def test_a_bad_number_of_failed_pieces_shows_an_error_and_books_nothing(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    for bad in ("abc", "-1", "1,5", "1000001", "9999999999"):
        response = send(prepared_client, form([6], (article_id, "3"), failed_pieces=bad))

        assert response.status_code == 422, bad
        assert field_error(response.text, "failed_pieces") == DE["production.error.failed_pieces"]
    assert productions() == []


def test_the_form_has_a_failed_pieces_field(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/new?prints=6").text

    assert 'name="failed_pieces"' in html
    assert DE["production.field.failed_pieces"] in html


# --- Case 6: filament --------------------------------------------------------------------------


def test_filament_is_stored_as_given_and_shown_with_colour_swatches(
    prepared_client: AppClient,
) -> None:
    book(prepared_client, [6, 7])

    items = stored_prints()
    assert (items[6].filament_type, items[6].filament_colors) == ("PLA", "#000000")
    assert (items[7].filament_type, items[7].filament_colors) == ("PETG", "#FFFFFF,#BB3D43")
    html = prepared_client.get("/production/1").text
    rows = re.findall(r'<tr class="detail-print.*?</tr>', html, re.S)
    assert "PLA" in rows[0] and 'fill="#000000"' in rows[0]
    assert "<title>#000000</title>" in rows[0]
    assert "PETG" in rows[1]
    assert 'fill="#FFFFFF"' in rows[1] and 'fill="#BB3D43"' in rows[1]


def test_bad_colour_codes_and_long_texts_never_reach_the_page(
    prepared_client: AppClient,
) -> None:
    book(prepared_client, [9])

    item = stored_prints()[9]
    assert item.filament_colors == "#12AB34"
    assert item.filament_type is not None and len(item.filament_type) <= 50
    html = prepared_client.get("/production/1").text
    assert "url(" not in html and "example.com" not in html and "#GGGGGG" not in html
    assert 'fill="#12AB34"' in html


def test_the_page_stays_usable_when_bambuddy_sends_odd_filament_fields(
    prepared_client: AppClient,
) -> None:
    odd = [dict(a, filament_type=["PLA"], filament_color=7) for a in ARCHIVES]
    serve(prepared_client, odd)

    book(prepared_client, [6])

    item = stored_prints()[6]
    assert item.filament_type is None and item.filament_colors is None


def test_absurd_amounts_from_bambuddy_count_as_unknown(prepared_client: AppClient) -> None:
    absurd = [dict(a, filament_used_grams=10**30, energy_kwh=-1) for a in ARCHIVES]
    serve(prepared_client, absurd)

    book(prepared_client, [6])

    item = stored_prints()[6]
    assert item.filament_grams is None and item.energy_kwh is None
    assert prepared_client.get("/production/1").status_code == 200


def test_the_client_reads_material_and_colours() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=fixture("archives.json"))

    archives = BambuddyClient(BASE, KEY, transport=httpx2.MockTransport(handler)).archives()

    assert [(a.filament_type, a.filament_color) for a in archives] == [
        ("PLA", "#FFFFFF,#BB3D43"),
        ("PETG", "#7F7E83"),
        ("PLA", "#000000"),
        ("PLA", None),
    ]


# --- Case 7: old productions and the migration -------------------------------------------------


def test_migration_keeps_old_productions_and_shows_the_new_fields_empty(tmp_path: Path) -> None:
    database = tmp_path / "quantities.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0018")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO productions (id, booked_on, created_at, note, total_cost, cancelled)"
            " VALUES (1, '2026-11-15', '2026-11-15 10:00:00', 'alt', '5.50', 0)"
        )
        connection.execute(
            "INSERT INTO production_prints (id, production_id, archive_id, material_cost,"
            " electricity_cost, wear_cost, machine_cost, status)"
            " VALUES (1, 1, 5, '3', '0.5', '2', '0', 'success')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT print_seconds, filament_grams, energy_kwh, filament_type, filament_colors"
            " FROM production_prints"
        ).fetchall() == [(None, None, None, None, None)]
        assert connection.execute(
            "SELECT failed_pieces, total_cost FROM productions"
        ).fetchall() == [(0, "5.50")]
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0018")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT material_cost FROM production_prints").fetchall() == [
            ("3",)
        ]
    run_migrations(engine)


def test_an_old_production_without_the_new_values_still_opens(client: AppClient) -> None:
    from datetime import date

    from app import production
    from app.production import PrintInput, ResultInput

    article_id = add_article()
    with db() as session, session.begin():
        production.book(
            session,
            None,
            date(2026, 11, 15),
            "",
            [PrintInput(5, D(1), D(1), D(1))],
            ResultInput(article_id, 1),
        )

    response = client.get("/production/1")

    assert response.status_code == 200
    assert DE["table.empty_value"] in response.text


# --- Case 8: cancelling ------------------------------------------------------------------------


def test_cancelling_keeps_the_stored_values(prepared_client: AppClient) -> None:
    book(prepared_client, [6], failed_pieces="1")

    response = prepared_client.post("/production/1/cancel", data={}, follow_redirects=False)

    assert response.status_code == 303
    item = stored_prints()[6]
    assert (item.print_seconds, item.filament_grams, item.energy_kwh) == (9000, D("40"), D("0.3"))
    assert (item.filament_type, item.filament_colors) == ("PLA", "#000000")
    (production_item,) = productions()
    assert production_item.cancelled and production_item.failed_pieces == 1
    with db() as session:
        assert len(list(session.scalars(select(StockMovement)))) == 2


# --- Texts -------------------------------------------------------------------------------------


def test_all_texts_exist_in_both_languages() -> None:
    keys = [
        "production.col.grams",
        "production.col.kwh",
        "production.col.filament",
        "production.field.failed_pieces",
        "production.error.failed_pieces",
        "production.detail.failed_pieces",
        "production.detail.totals",
    ]
    for key in keys:
        assert DE.get(key) and EN.get(key), key
