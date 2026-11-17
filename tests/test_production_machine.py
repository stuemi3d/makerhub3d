"""Production: machine costs instead of wear (issue #135), rate of the printer device (issue #162).
All data is invented; Bambuddy answers come from a mock. The test case numbers of the part
"Printer device" refer to the comment on issue #162.
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
from app.models import AppSettings, Device, ProductionPrint
from tests.auth import AppClient
from tests.machine import add_dryer, add_printer
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
    """Gives the one printer of the test the hourly rate `value`."""
    with db() as session, session.begin():
        session.scalars(select(Device).where(Device.kind == "printer")).one().price = D(value) * D(
            5000
        )


ARCHIVES_1H = [
    raw(7, "2026-11-15T10:00:00", name="Druck B", cost=2.0, energy=0.25),
    raw(6, "2026-11-15T09:00:00", name="Druck A", cost=3.0, energy=0.5),
]


@pytest.fixture
def prepared_client(client: AppClient) -> AppClient:
    """Print 6 ("Druck A") lasted 1 h 5 min (3900 s)."""
    prepared(client, ARCHIVES_1H)
    add_printer("12.00")
    return client


def input_value(html: str, name: str) -> str | None:
    match = re.search(rf'<input\b[^>]*name="{name}"[^>]*value="([^"]*)"', html)
    return match.group(1) if match else None


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


def test_without_a_printer_the_field_stays_empty_with_a_hint(client: AppClient) -> None:
    prepared(client, ARCHIVES_1H)

    html = client.get("/production/new?prints=6").text

    assert input_value(html, "cost_machine_6") == ""
    assert DE["production.machine_missing.device"] in html
    add_printer("12.00", bambuddy_printer_id=None)
    html = client.get("/production/new?prints=6").text
    assert DE["production.machine_missing.device"] in html


def test_an_unknown_duration_leaves_the_field_empty_with_a_hint(client: AppClient) -> None:
    unknown = raw(6, name="Druck ohne Dauer")
    unknown["print_time_seconds"] = None
    unknown["actual_time_seconds"] = None
    prepared(client, [unknown])
    add_printer("12.00")

    html = client.get("/production/new?prints=6").text

    assert input_value(html, "cost_machine_6") == ""
    assert DE["production.machine_missing.duration"] in html
    assert DE["production.machine_missing.device"] not in html


def test_a_known_duration_and_rate_show_no_hint(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/new?prints=6").text

    assert DE["production.machine_missing.device"] not in html
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
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0017")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT wear_cost FROM production_prints").fetchall() == [("2",)]
    run_migrations(engine)


# --- Case 10: texts ----------------------------------------------------------------------------


def test_all_machine_texts_exist_in_both_languages() -> None:
    keys = [
        "production.col.machine",
        "production.machine_missing.device",
        "production.machine_missing.duration",
        "production.field.device",
        "production.error.device",
        "production.error.no_printer",
    ]
    for key in keys:
        assert DE.get(key) and EN.get(key), key


# --- Issue #162: the rate of the printer device -------------------------------------------------


def select_options(html: str, name: str) -> list[tuple[str, bool]]:
    """(value, selected) of every option of the select field `name`."""
    block = re.search(rf'<select\b[^>]*name="{name}".*?</select>', html, re.S)
    assert block, name
    return [
        (value, "selected" in attrs)
        for value, attrs in re.findall(r'<option value="([^"]*)"([^>]*)>', block.group(0))
    ]


def test_a_known_printer_is_preselected_and_the_rate_includes_the_drying_surcharge(
    client: AppClient,
) -> None:
    """Case 1: 12.00 EUR/h plus 0.10 EUR/h drying (100 EUR per year over 1000 h), 3900 s."""
    prepared(client, ARCHIVES_1H)
    printer_id = add_printer("12.00")
    add_dryer("100")

    html = client.get("/production/new?prints=6").text

    assert (str(printer_id), True) in select_options(html, "device_6")
    assert input_value(html, "cost_machine_6") == "13,1083"  # 3900 / 3600 x 12.10


def test_the_booked_machine_costs_are_the_rate_of_the_preselected_printer(
    client: AppClient,
) -> None:
    prepared(client, ARCHIVES_1H)
    add_printer("12.00")
    add_dryer("100")

    send(client, form([6], (add_article(), "5")))

    assert productions()[0].prints[0].machine_cost == D("13.1083")  # 1.08333 x 12.10


def test_a_changed_proposal_is_stored_as_typed(prepared_client: AppClient) -> None:
    """Case 2 (the existing test above also covers it with the preselected printer)."""
    send(prepared_client, form([6], (add_article(), "5"), cost_machine_6="7,5"))

    assert productions()[0].prints[0].machine_cost == D("7.5")


def test_choosing_another_printer_gives_the_proposal_of_that_printer(
    prepared_client: AppClient,
) -> None:
    """Case 3: the page is drawn again with the proposal of the printer that was chosen."""
    other = add_printer("30.00", bambuddy_printer_id=2, name="Zweiter Drucker")

    html = prepared_client.post(
        "/production/new",
        data={
            "prints": ["6"],
            "device_6": str(other),
            "cost_machine_6": "13",
            "action": "device",
            "changed": "6",
        },
    ).text

    assert (str(other), True) in select_options(html, "device_6")
    assert input_value(html, "cost_machine_6") == "32,5"  # 3900 / 3600 x 30


def test_choosing_another_printer_keeps_what_was_typed_elsewhere(
    prepared_client: AppClient,
) -> None:
    other = add_printer("30.00", bambuddy_printer_id=2, name="Zweiter Drucker")

    response = prepared_client.post(
        "/production/new",
        data={
            "prints": ["6"],
            "device_6": str(other),
            "cost_machine_6": "13",
            "cost_material_6": "4,25",
            "note": "Notiz",
            "action": "device",
            "changed": "6",
        },
    )

    assert response.status_code == 200
    assert input_value(response.text, "cost_material_6") == "4,25"
    assert "Notiz" in response.text
    assert field_error(response.text, "article") is None
    assert productions() == []


def test_a_booking_with_another_printer_uses_what_the_form_sent(
    prepared_client: AppClient,
) -> None:
    other = add_printer("30.00", bambuddy_printer_id=2, name="Zweiter Drucker")

    send(prepared_client, form([6], (add_article(), "5"), device_6=str(other)))

    assert productions()[0].prints[0].machine_cost == D("32.5")


def test_without_a_matching_printer_the_device_is_a_required_choice(client: AppClient) -> None:
    """Case 4: the Bambuddy printer (1) is not linked to any device."""
    prepared(client, ARCHIVES_1H)
    add_printer("12.00", bambuddy_printer_id=None)

    html = client.get("/production/new?prints=6").text
    assert all(not chosen for _, chosen in select_options(html, "device_6"))

    response = send(client, form([6], (add_article(), "5"), device_6=""))

    assert response.status_code == 422
    assert field_error(response.text, "device_6") == DE["production.error.device"]
    assert productions() == []


def test_a_print_without_a_printer_in_the_archive_needs_a_choice_too(client: AppClient) -> None:
    prepared(client, [raw(6, printer_id=None)])
    printer_id = add_printer("12.00")

    html = client.get("/production/new?prints=6").text
    assert all(not chosen for _, chosen in select_options(html, "device_6"))

    send(client, form([6], (add_article(), "5"), device_6=str(printer_id)))

    assert productions()[0].prints[0].machine_cost == D("13")


def test_a_device_that_does_not_exist_is_refused(prepared_client: AppClient) -> None:
    response = send(prepared_client, form([6], (add_article(), "5"), device_6="999"))

    assert response.status_code == 422
    assert field_error(response.text, "device_6") == DE["production.error.device"]
    assert productions() == []


def test_the_choice_lists_only_active_printers(prepared_client: AppClient) -> None:
    """Case 5."""
    add_printer("5.00", bambuddy_printer_id=None, name="Abgeschaltet", active=False)
    dryer = add_dryer("100")

    values = [
        value
        for value, _ in select_options(
            prepared_client.get("/production/new?prints=6").text, "device_6"
        )
    ]

    assert str(dryer) not in values
    assert len(values) == 2  # the empty choice and the one active printer


def test_a_deactivated_linked_printer_counts_as_not_assigned(client: AppClient) -> None:
    prepared(client, ARCHIVES_1H)
    old = add_printer("12.00", active=False)

    html = client.get("/production/new?prints=6").text

    assert str(old) not in [value for value, _ in select_options(html, "device_6")]
    response = send(client, form([6], (add_article(), "5"), device_6=str(old)))
    assert response.status_code == 422
    assert field_error(response.text, "device_6")


def test_without_any_printer_there_is_a_notice_and_no_booking(client: AppClient) -> None:
    """Case 6."""
    prepared(client, ARCHIVES_1H)

    html = client.get("/production/new?prints=6").text
    assert DE["production.error.no_printer"] in html

    response = send(client, form([6], (add_article(), "5")))
    assert response.status_code == 422
    assert DE["production.error.no_printer"] in response.text
    assert productions() == []


def test_a_later_change_of_a_printer_does_not_change_old_productions(
    prepared_client: AppClient,
) -> None:
    """Case 7."""
    send(prepared_client, form([6], (add_article(), "5")))

    set_rate("99.00")

    assert productions()[0].prints[0].machine_cost == D("13")


def test_the_settings_page_has_no_printer_rate_any_more(client: AppClient) -> None:
    """Case 8."""
    html = client.get("/settings").text

    assert 'name="machine_rate"' not in html
    assert not hasattr(AppSettings, "machine_rate")


def test_migration_drops_the_rate_and_keeps_old_productions(tmp_path: Path) -> None:
    """Case 8: old productions keep their machine costs; the step can be undone."""
    database = tmp_path / "rate.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0023")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE app_settings SET machine_rate = '12'")
        connection.execute(
            "INSERT INTO productions (id, booked_on, created_at, note, total_cost, cancelled)"
            " VALUES (1, '2026-11-15', '2026-11-15 10:00:00', 'alt', '5.50', 0)"
        )
        connection.execute(
            "INSERT INTO production_prints (id, production_id, archive_id, material_cost,"
            " electricity_cost, wear_cost, machine_cost, status)"
            " VALUES (1, 1, 5, '3', '0.5', '0', '2', 'success')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(app_settings)")]
        assert "machine_rate" not in columns
        assert connection.execute("SELECT machine_cost FROM production_prints").fetchall() == [
            ("2",)
        ]
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0023")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT machine_rate FROM app_settings").fetchall() == [("0",)]
    run_migrations(engine)


def test_a_print_without_duration_leaves_the_field_empty_but_keeps_the_printer(
    client: AppClient,
) -> None:
    """Case 9: a value can still be typed by hand."""
    unknown = raw(6, name="Druck ohne Dauer")
    unknown["print_time_seconds"] = None
    unknown["actual_time_seconds"] = None
    prepared(client, [unknown])
    printer_id = add_printer("12.00")

    html = client.get("/production/new?prints=6").text
    assert (str(printer_id), True) in select_options(html, "device_6")
    assert input_value(html, "cost_machine_6") == ""

    send(client, form([6], (add_article(), "5"), cost_machine_6="4"))
    assert productions()[0].prints[0].machine_cost == D("4")


def test_in_the_popup_the_choice_of_a_printer_draws_the_form_again(
    prepared_client: AppClient,
) -> None:
    """Case 3: the select of the pop-up sends the form with the action "device"."""
    html = prepared_client.get("/production/new?prints=6", headers={"HX-Request": "true"}).text

    tag = re.search(r'<select\b[^>]*name="device_6"[^>]*>', html).group(0)
    assert 'hx-post="/production/new"' in tag
    assert '"action": "device"' in tag and '"changed": "6"' in tag
    assert 'hx-select="#production-form"' in tag
