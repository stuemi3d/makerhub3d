"""Production: machine costs instead of wear (issue #135). All data is invented; Bambuddy
answers come from a mock. The test case numbers refer to the comment on the issue.
"""

import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select

from app.db import alembic_config, make_engine, run_migrations
from app.models import AppSettings, ChangeLogEntry, ProductionPrint
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.forms import valid_form
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_booking import (
    field_error,
    form,
    productions,
    send,
)
from tests.test_production_page import prepared, raw
from tests.test_stock import add_article, db

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


def set_rate(value: str) -> None:
    with db() as session, session.begin():
        session.get(AppSettings, 1).machine_rate = D(value)


ARCHIVES_1H = [
    raw(7, "2026-11-15T10:00:00", name="Druck B", cost=2.0, energy=0.25),
    raw(6, "2026-11-15T09:00:00", name="Druck A", cost=3.0, energy=0.5),
]


@pytest.fixture
def prepared_client(client: AppClient) -> AppClient:
    """Print 6 ("Druck A") lasted 1 h 5 min (3900 s)."""
    prepared(client, ARCHIVES_1H)
    set_rate("12.00")
    return client


def input_value(html: str, name: str) -> str | None:
    match = re.search(rf'<input\b[^>]*name="{name}"[^>]*value="([^"]*)"', html)
    return match.group(1) if match else None


# --- Case 1: the setting -----------------------------------------------------------------------


def test_the_rate_starts_at_zero_and_is_shown_on_the_settings_page(client: AppClient) -> None:
    html = client.get("/settings").text

    assert DE["settings.field.machine_rate"] in html
    assert input_value(html, "machine_rate") == "0"


def test_an_admin_saves_the_rate_with_the_usual_checks_and_a_log_entry(client: AppClient) -> None:
    response = client.post(
        "/settings", data=valid_form(machine_rate="12,5"), follow_redirects=False
    )

    assert response.status_code == 303
    with db() as session:
        assert session.get(AppSettings, 1).machine_rate == D("12.5")
        entry = session.scalars(
            select(ChangeLogEntry).where(ChangeLogEntry.action == "settings.changed")
        ).one()
        assert {"field": "machine_rate", "old": "0", "new": "12.5"} in json.loads(entry.details)
    assert input_value(client.get("/settings").text, "machine_rate") == "12,5"


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("abc", "settings.error.number"),
        ("-1", "settings.error.negative"),
        ("1,23456", "settings.error.decimals"),
        ("", "settings.error.required"),
        ("10001", "settings.error.too_large"),
    ],
)
def test_a_bad_rate_is_refused_and_nothing_is_saved(
    client: AppClient, value: str, error: str
) -> None:
    response = client.post("/settings", data=valid_form(machine_rate=value))

    assert response.status_code == 422
    assert field_error(response.text, "machine_rate") == DE[error]
    with db() as session:
        assert session.get(AppSettings, 1).machine_rate == D(0)


def test_four_decimals_are_accepted(client: AppClient) -> None:
    assert (
        client.post(
            "/settings", data=valid_form(machine_rate="1,2345"), follow_redirects=False
        ).status_code
        == 303
    )
    with db() as session:
        assert session.get(AppSettings, 1).machine_rate == D("1.2345")


@pytest.mark.parametrize("username", [EMPLOYEE_NAME, READER_NAME])
def test_only_an_admin_may_change_the_rate(client: AppClient, username: str) -> None:
    role = "employee" if username == EMPLOYEE_NAME else "read_only"
    add_user(username, role)
    with client_as(username) as other:
        response = other.post("/settings", data=valid_form(machine_rate="9"))
        assert response.status_code == 403
    with db() as session:
        assert session.get(AppSettings, 1).machine_rate == D(0)


# --- Case 2: the form --------------------------------------------------------------------------


def test_the_form_prefills_machine_costs_from_duration_and_rate(
    prepared_client: AppClient,
) -> None:
    html = prepared_client.get("/production/new?prints=6").text

    assert DE["production.col.machine"] in html
    assert input_value(html, "cost_machine_6") == "13"
    assert 'name="cost_wear_6"' not in html
    assert "1 h 5 min" in html


def test_the_prefilled_machine_value_can_be_changed_by_hand(prepared_client: AppClient) -> None:
    article_id = add_article()

    send(prepared_client, form([6], (article_id, "5"), cost_machine_6="2,5"))

    (item,) = productions()
    assert item.prints[0].machine_cost == D("2.5")
    assert item.total_cost == D("6.00")  # 3.00 + 0.50 + 2.50


# --- Case 3: no wear from Bambuddy -------------------------------------------------------------


def test_wear_from_bambuddy_is_shown_and_counted_nowhere(prepared_client: AppClient) -> None:
    article_id = add_article()
    html = prepared_client.get("/production").text
    new_html = prepared_client.get("/production/new?prints=6").text

    assert DE["production.col.wear"] not in html
    assert DE["production.col.wear"] not in new_html

    send(prepared_client, form([6], (article_id, "5")))

    (item,) = productions()
    assert item.prints[0].wear_cost == D(0)
    assert item.total_cost == D("16.50")  # 3.00 + 0.50 + 13.00, the wear of 1.00 is ignored


# --- Case 4: no rate or no duration ------------------------------------------------------------


def test_rate_zero_leaves_the_field_empty_with_a_hint(client: AppClient) -> None:
    prepared(client, ARCHIVES_1H)

    html = client.get("/production/new?prints=6").text

    assert input_value(html, "cost_machine_6") == ""
    assert DE["production.machine_missing.rate"] in html
    article_id = add_article()
    send(client, form([6], (article_id, "5"), cost_machine_6=""))
    assert productions()[0].prints[0].machine_cost == D(0)


def test_an_unknown_duration_leaves_the_field_empty_with_a_hint(client: AppClient) -> None:
    unknown = raw(6, name="Druck ohne Dauer")
    unknown["print_time_seconds"] = None
    unknown["actual_time_seconds"] = None
    prepared(client, [unknown])
    set_rate("12.00")

    html = client.get("/production/new?prints=6").text

    assert input_value(html, "cost_machine_6") == ""
    assert DE["production.machine_missing.duration"] in html
    assert DE["production.machine_missing.rate"] not in html


def test_a_known_duration_and_rate_show_no_hint(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/new?prints=6").text

    assert DE["production.machine_missing.rate"] not in html
    assert DE["production.machine_missing.duration"] not in html


# --- Case 5: total, rounding, stored as value --------------------------------------------------


def test_the_total_is_material_power_and_machine_rounded_to_the_cent(
    prepared_client: AppClient,
) -> None:
    set_rate("12.3456")  # 3900 s -> 13.3744
    article_id = add_article()

    send(prepared_client, form([6], (article_id, "5")))

    (item,) = productions()
    assert item.prints[0].machine_cost == D("13.3744")
    assert item.total_cost == D("16.87")
    assert item.results[0].allocated_cost == D("16.87")


def test_the_preview_includes_machine_costs_and_stores_nothing(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()

    response = send(prepared_client, form([6], (article_id, "5"), action="preview"))

    assert response.status_code == 200
    assert "16,5" in response.text
    assert productions() == []


def test_a_later_change_of_the_rate_does_not_change_old_productions(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    send(prepared_client, form([6], (article_id, "5")))

    set_rate("99.00")

    (item,) = productions()
    assert (item.prints[0].machine_cost, item.total_cost) == (D("13"), D("16.50"))


def test_labour_still_counts_in_the_total(prepared_client: AppClient) -> None:
    article_id = add_article()

    send(prepared_client, form([6], (article_id, "5"), labor_minutes="60", labor_rate="30"))

    assert productions()[0].total_cost == D("46.50")


# --- Case 6: bad input -------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["abc", "-1", "1,23456", "1e3"])
def test_bad_machine_costs_show_an_error_at_the_field_and_book_nothing(
    prepared_client: AppClient, value: str
) -> None:
    article_id = add_article()

    response = send(prepared_client, form([6], (article_id, "5"), cost_machine_6=value))

    assert response.status_code == 422
    assert field_error(response.text, "cost_machine_6")
    assert input_value(response.text, "cost_machine_6") == value
    assert productions() == []


# --- Case 7: detail page -----------------------------------------------------------------------


def test_the_detail_page_shows_machine_and_no_wear_column_for_new_productions(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    send(prepared_client, form([6], (article_id, "5")))

    html = prepared_client.get("/production/1").text

    assert DE["production.col.machine"] in html
    assert DE["production.col.wear"] not in html
    assert "13" in html and "16,5" in html


def test_the_detail_page_keeps_showing_old_wear(prepared_client: AppClient) -> None:
    article_id = add_article()
    send(prepared_client, form([6], (article_id, "5")))
    with db() as session, session.begin():
        session.scalars(select(ProductionPrint)).one().wear_cost = D("1.00")

    html = prepared_client.get("/production/1").text

    assert DE["production.col.wear"] in html
    assert DE["production.col.machine"] in html


# --- Case 8: migration -------------------------------------------------------------------------


def test_migration_keeps_old_productions_and_can_be_undone(tmp_path: Path) -> None:
    database = tmp_path / "machine.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0017")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO productions (id, booked_on, created_at, note, total_cost, cancelled)"
            " VALUES (1, '2026-11-15', '2026-11-15 10:00:00', 'alt', '5.50', 0)"
        )
        connection.execute(
            "INSERT INTO production_prints (id, production_id, archive_id, material_cost,"
            " electricity_cost, wear_cost, status) VALUES (1, 1, 5, '3', '0.5', '2', 'success')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT material_cost, electricity_cost, wear_cost, machine_cost FROM production_prints"
        ).fetchall() == [("3", "0.5", "2", "0")]
        assert connection.execute("SELECT total_cost FROM productions").fetchall() == [("5.50",)]
        assert connection.execute("SELECT machine_rate FROM app_settings").fetchall() == [("0",)]
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0017")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT wear_cost FROM production_prints").fetchall() == [("2",)]
    run_migrations(engine)


# --- Case 10: texts ----------------------------------------------------------------------------


def test_all_machine_texts_exist_in_both_languages() -> None:
    keys = [
        "settings.field.machine_rate",
        "settings.hint.machine_rate",
        "production.col.machine",
        "production.machine_missing.rate",
        "production.machine_missing.duration",
    ]
    for key in keys:
        assert DE.get(key) and EN.get(key), key
