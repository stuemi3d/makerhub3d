"""Price rule and filament in the calculation form, in a real browser on desktop and phone
(issue #237). The filament is made for the test straight in the database; Bambuddy is played
by the fake server of the calculation test."""

import sqlite3

from playwright.sync_api import Page, expect

from app.config import DATABASE_FILE
from tests.browser.conftest import LiveServer
from tests.browser.test_calculation import (  # noqa: F401
    choose_and_wait,
    fake_bambuddy,
    link_model,
    open_new,
    prepare,
    type_and_wait,
)
from tests.browser.test_pages import fits_screen_width


def make_filament(live_server: LiveServer, color: str, standard: str) -> int:
    with sqlite3.connect(live_server.data_dir / DATABASE_FILE) as connection:
        cursor = connection.execute(
            "INSERT INTO filaments (brand, material, subtype, color_name, color, standard_price,"
            " note, active, identity_key, created_at)"
            " VALUES ('Beispielmarke', 'PLA', '', ?, '', ?, '', 1, ?, '2026-11-21 10:00:00')",
            (color, standard, f"beispielmarke|pla||{color.casefold()}"),
        )
        return int(cursor.lastrowid)


def test_a_filament_with_the_standard_price_is_chosen_and_calculated(
    admin_page: Page,
    live_server: LiveServer,
    fake_bambuddy: str,  # noqa: F811
    viewport_name: str,
) -> None:
    color = f"Preisregel {viewport_name}"
    filament = make_filament(live_server, color, "25")
    printer, _ = prepare(admin_page, live_server, fake_bambuddy, viewport_name, "f")
    admin_page.goto("/calculation")
    dialog = open_new(admin_page)

    choose_and_wait(admin_page, admin_page.locator("#printer"), label=printer)
    type_and_wait(admin_page, admin_page.locator("#print_minutes"), "60")
    type_and_wait(admin_page, admin_page.locator("#pieces"), "2")
    choose_and_wait(admin_page, admin_page.locator("#material_0"), label="PLA")
    type_and_wait(admin_page, admin_page.locator("#grams_0"), "100")

    # The rule starts as "single spool"; with the standard price the filament is asked for
    expect(admin_page.locator("#rule_0")).to_have_value("spool")
    choose_and_wait(admin_page, admin_page.locator("#rule_0"), value="standard")
    expect(admin_page.locator("#spool_0_0")).to_have_count(0)
    expect(admin_page.locator("#res-suggested")).to_have_count(0)
    choose_and_wait(admin_page, admin_page.locator("#filament_0"), value=str(filament))

    # 100 g x 25 EUR/kg
    expect(admin_page.locator("#res-material")).to_have_text("2,50")
    assert fits_screen_width(admin_page)
    expect(dialog.locator("#rule_0")).to_have_value("standard")
