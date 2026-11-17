"""The calculation list and its pop-ups in a real browser, on desktop and phone (issues #161, #186).

Bambuddy is played by a tiny server on 127.0.0.1 that answers with invented spools; the test
app (a separate process) reads from it, the browser only talks to the test app. The printer and
its model are made for the test (the browser tests share one database, so every viewport has
its own names and only changes are checked).
"""

import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.sync_api import Locator, Page, expect

from app.config import DATABASE_FILE
from tests.browser.conftest import HOST, LiveServer, t
from tests.browser.test_pages import fits_screen_width
from tests.browser.test_production import connect, create_printer

SPOOLS = [
    {
        "id": 1,
        "material": "PLA",
        "color_name": "Weiß",
        "brand": "Beispielmarke",
        "cost_per_kg": 20,
        "label_weight": 1000,
        "weight_used": 0,
        "category": "Geschäftlich",
    },
    {
        "id": 2,
        "material": "PLA",
        "color_name": "Fast leer",
        "brand": "Beispielmarke",
        "cost_per_kg": 30,
        "label_weight": 100,
        "weight_used": 60,
        "category": "Geschäftlich",
    },
]


class FakeBambuddy(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if "inventory/spools" in self.path:
            payload: object = SPOOLS
        elif "settings" in self.path:
            payload = {"energy_cost_per_kwh": 0.3, "currency": "EUR"}
        else:
            payload = []
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def fake_bambuddy() -> Iterator[str]:
    server = HTTPServer((HOST, 0), FakeBambuddy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://{HOST}:{server.server_port}"
    server.shutdown()
    server.server_close()


def link_model(live_server: LiveServer, printer: str, model: str) -> None:
    """Gives the printer a model with a power table (PLA 0.10 kWh/h)."""
    with sqlite3.connect(live_server.data_dir / DATABASE_FILE) as connection:
        cursor = connection.execute(
            "INSERT INTO printer_models (name, created_at) VALUES (?, '2026-11-17 10:00:00')",
            (model,),
        )
        connection.execute(
            "INSERT INTO printer_model_powers (model_id, material, kwh_per_hour, position)"
            " VALUES (?, 'PLA', '0.10', 0)",
            (cursor.lastrowid,),
        )
        connection.execute(
            "UPDATE devices SET printer_model_id = ? WHERE name = ?", (cursor.lastrowid, printer)
        )


def settle(page: Page) -> None:
    page.wait_for_function(
        "() => !document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )


def type_and_wait(page: Page, field: Locator, value: str) -> None:
    """Types a value, leaves the field and waits until the form was drawn again."""
    with page.expect_response(
        lambda r: r.url.endswith("/calculation/new") and r.request.method == "POST"
    ):
        field.fill(value)
        field.press("Tab")
    settle(page)


def choose_and_wait(page: Page, select: Locator, **choice: str) -> None:
    with page.expect_response(
        lambda r: r.url.endswith("/calculation/new") and r.request.method == "POST"
    ):
        select.select_option(**choice)
    settle(page)


def create_article(page: Page, number: str, name: str) -> str:
    """An article made by hand in its form; returns the address of its page."""
    page.goto("/articles/new")
    page.fill("#number", number)
    page.fill("#name", name)
    page.locator("main form button[type=submit]").click()
    expect(page.locator("h1")).to_contain_text(number)
    return page.url


def prepare(
    page: Page, live_server: LiveServer, fake_bambuddy: str, viewport: str, tag: str
) -> tuple[str, str]:
    connect(page, fake_bambuddy)
    printer, model = f"Kalk-Gerät {tag} {viewport}", f"Kalk-Modell {tag} {viewport}"
    create_printer(page, printer, "1000")
    link_model(live_server, printer, model)
    article = create_article(page, f"KALK-{tag}-{viewport}", "Kalkulierter Halter")
    return printer, article


def open_new(page: Page, article_number: str | None = None) -> Locator:
    """The pop-up of "Neue Kalkulation" from the list."""
    page.get_by_role("button", name=t("calc.new")).first.click()
    dialog = page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    expect(dialog.locator("#calc-form")).to_be_visible()
    return dialog


def fill_valid_form(page: Page, printer: str, grams: str = "30") -> None:
    choose_and_wait(page, page.locator("#printer"), label=printer)
    type_and_wait(page, page.locator("#print_minutes"), "60")
    type_and_wait(page, page.locator("#pieces"), "2")
    choose_and_wait(page, page.locator("#material_0"), label="PLA")
    type_and_wait(page, page.locator("#grams_0"), grams)
    choose_and_wait(page, page.locator("#spool_0_0"), value="1")
    expect(page.locator("#res-suggested")).to_be_visible()


def test_the_new_calculation_pop_up_with_hints_two_spools_and_time_shares(
    admin_page: Page, live_server: LiveServer, fake_bambuddy: str, viewport_name: str
) -> None:
    printer, _ = prepare(admin_page, live_server, fake_bambuddy, viewport_name, "a")
    admin_page.goto("/calculation")
    assert fits_screen_width(admin_page)
    dialog = open_new(admin_page)
    assert fits_screen_width(admin_page)

    # A quick calculation: no article, so no save button, but a hint
    expect(dialog.locator("button[value=save]")).to_have_count(0)
    expect(dialog.get_by_text(t("calc.spool.choose_material"))).to_be_visible()
    # One material needs no time share
    expect(dialog.locator("#minutes_0")).to_have_count(0)

    choose_and_wait(admin_page, admin_page.locator("#printer"), label=printer)
    type_and_wait(admin_page, admin_page.locator("#print_minutes"), "60")
    type_and_wait(admin_page, admin_page.locator("#pieces"), "2")
    choose_and_wait(admin_page, admin_page.locator("#material_0"), label="PLA")
    expect(dialog.get_by_text(t("calc.spool.choose_material"))).to_have_count(0)
    type_and_wait(admin_page, admin_page.locator("#grams_0"), "100")

    # The small spool (40 g left) does not hold 100 g: a second spool field appears
    choose_and_wait(admin_page, admin_page.locator("#spool_0_0"), value="2")
    expect(admin_page.locator("#spool_0_1")).to_be_visible()
    expect(admin_page.locator("#res-suggested")).to_have_count(0)
    choose_and_wait(admin_page, admin_page.locator("#spool_0_1"), value="1")
    expect(admin_page.locator("#res-suggested")).to_have_text(re.compile(r"^\d+,\d{2}$"))
    # 40 g x 30 EUR/kg + 60 g x 20 EUR/kg
    expect(admin_page.locator("#res-material")).to_have_text("2,40")
    assert fits_screen_width(admin_page)

    # A second material brings the time share fields; the first one has the print time
    with admin_page.expect_response(
        lambda r: r.url.endswith("/calculation/new") and r.request.method == "POST"
    ):
        dialog.get_by_role("button", name=t("calc.add_position")).click()
    settle(admin_page)
    expect(admin_page.locator("#material_1")).to_be_visible()
    expect(admin_page.locator("#minutes_0")).to_have_value("60")
    expect(admin_page.locator("#grams_0")).to_have_value("100")
    assert fits_screen_width(admin_page)

    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()


def test_save_view_copy_make_main_and_delete_in_popups(
    admin_page: Page, live_server: LiveServer, fake_bambuddy: str, viewport_name: str
) -> None:
    printer, article = prepare(admin_page, live_server, fake_bambuddy, viewport_name, "b")
    number = f"KALK-b-{viewport_name}"

    # The tile of the article leads to the list filtered on it
    admin_page.goto(article)
    admin_page.locator("a.tile", has_text=t("article.tile.calculations")).click()
    expect(admin_page).to_have_url(re.compile(r"/calculation\?article=\d+"))
    expect(admin_page.locator("tr.calc-row")).to_have_count(0)

    # New calculation: the article is preselected; save
    dialog = open_new(admin_page)
    expect(dialog.locator("#article")).to_contain_text(number)
    assert (
        dialog.locator("#article")
        .evaluate("(select) => select.selectedOptions[0].text")
        .startswith(number)
    )
    fill_valid_form(admin_page, printer)
    expect(dialog.locator("button[value=save]")).to_be_visible()
    assert fits_screen_width(admin_page)
    dialog.locator("button[value=save]").click()
    expect(admin_page.get_by_text(t("calc.notice.saved"))).to_be_visible()
    rows = admin_page.locator("tr.calc-row")
    expect(rows).to_have_count(1)
    expect(rows.first.locator(".chip")).to_have_text(t("calc.chip.main"))
    expect(rows.first).to_contain_text(number)
    assert fits_screen_width(admin_page)

    # View in a pop-up
    rows.first.locator("a.icon-btn", has_text=t("table.action.view")).click()
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text(t("calc.result.suggested"))).to_be_visible()
    assert fits_screen_width(admin_page)
    dialog.get_by_role("button", name=t("calc.view.close")).click()
    expect(dialog).not_to_be_visible()

    # Copy in a pop-up: values filled in; saving makes a second one that is not the main one
    rows.first.locator("a.icon-btn", has_text=t("table.action.copy")).click()
    expect(dialog.locator("#grams_0")).to_have_value("30")
    expect(dialog.locator("#res-suggested")).to_be_visible()
    type_and_wait(admin_page, admin_page.locator("#grams_0"), "40")
    dialog.locator("button[value=save]").click()
    expect(rows).to_have_count(2)
    expect(admin_page.locator("tr.calc-row .chip")).to_have_count(1)
    assert fits_screen_width(admin_page)

    # Make the other one the main one
    other = admin_page.locator("tr.calc-row").filter(
        has=admin_page.get_by_role("button", name=t("table.action.main"), exact=False)
    )
    other.get_by_role("button", name=t("table.action.main"), exact=False).click()
    expect(admin_page.get_by_text(t("calc.notice.main"))).to_be_visible()
    expect(admin_page.locator("tr.calc-row .chip")).to_have_count(1)

    # Edit in a pop-up: the article cannot be changed; saving keeps the list
    rows.first.locator("a.icon-btn", has_text=t("table.action.edit")).click()
    expect(dialog.locator("#calc-form")).to_be_visible()
    expect(dialog.locator("select#article")).to_have_count(0)
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()

    # Delete in a pop-up, no browser dialog
    rows.first.locator("a.icon-btn", has_text=t("table.action.delete")).click()
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    dialog.get_by_role("button", name=t("calc.delete.button")).click()
    expect(dialog).not_to_be_visible()
    expect(rows).to_have_count(1)
    assert fits_screen_width(admin_page)


def test_filling_from_top_to_bottom_shows_no_early_errors_and_fields_stand_level(
    admin_page: Page, live_server: LiveServer, fake_bambuddy: str, viewport_name: str
) -> None:
    """Issue #188: nothing red for fields that are still empty; the buttons mark what is missing."""
    printer, _ = prepare(admin_page, live_server, fake_bambuddy, viewport_name, "c")
    admin_page.goto("/calculation")
    dialog = open_new(admin_page)
    invalid = dialog.locator("[aria-invalid=true]")
    expect(invalid).to_have_count(0)

    type_and_wait(admin_page, admin_page.locator("#print_minutes"), "10")
    expect(invalid).to_have_count(0)
    choose_and_wait(admin_page, admin_page.locator("#printer"), label=printer)
    expect(invalid).to_have_count(0)

    # Print time and pieces stand on one level, on the desktop and on the phone
    first = admin_page.locator("#print_minutes").bounding_box()
    second = admin_page.locator("#pieces").bounding_box()
    assert abs(first["y"] - second["y"]) <= 1
    assert abs(first["height"] - second["height"]) <= 1
    assert fits_screen_width(admin_page)

    # The button marks what is missing
    dialog.get_by_role("button", name=t("calc.calculate")).click()
    expect(admin_page.locator("#pieces[aria-invalid=true]")).to_be_visible()
    settle(admin_page)

    # Typing on clears the mark and goes on without early errors
    type_and_wait(admin_page, admin_page.locator("#pieces"), "2")
    expect(admin_page.locator("#pieces[aria-invalid=true]")).to_have_count(0)
    choose_and_wait(admin_page, admin_page.locator("#material_0"), label="PLA")
    expect(invalid).to_have_count(0)
    type_and_wait(admin_page, admin_page.locator("#grams_0"), "30")
    expect(invalid).to_have_count(0)
    choose_and_wait(admin_page, admin_page.locator("#spool_0_0"), value="1")
    expect(admin_page.locator("#res-suggested")).to_be_visible()
    first = admin_page.locator("#material_0").bounding_box()
    second = admin_page.locator("#grams_0").bounding_box()
    assert abs(first["y"] - second["y"]) <= 1
    assert fits_screen_width(admin_page)
