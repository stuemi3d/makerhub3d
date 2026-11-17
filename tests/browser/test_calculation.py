"""Page "New calculation" in a real browser, on desktop and phone (issue #161, test case 16).

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


def test_calculate_by_hand_with_two_spools(
    admin_page: Page, live_server: LiveServer, fake_bambuddy: str, viewport_name: str
) -> None:
    connect(admin_page, fake_bambuddy)
    printer, model = f"Kalk-Gerät {viewport_name}", f"Kalk-Modell {viewport_name}"
    create_printer(admin_page, printer, "1000")
    link_model(live_server, printer, model)

    admin_page.goto("/calculation/new")
    assert fits_screen_width(admin_page)
    expect(admin_page.locator("#res-suggested")).to_have_count(0)

    choose_and_wait(admin_page, admin_page.locator("#printer"), label=printer)
    type_and_wait(admin_page, admin_page.locator("#print_minutes"), "60")
    type_and_wait(admin_page, admin_page.locator("#pieces"), "2")
    choose_and_wait(admin_page, admin_page.locator("#material_0"), label="PLA")
    type_and_wait(admin_page, admin_page.locator("#grams_0"), "100")
    type_and_wait(admin_page, admin_page.locator("#minutes_0"), "60")

    # Nothing is chosen yet: no result, and a note that the position is incomplete
    expect(admin_page.locator("#res-suggested")).to_have_count(0)
    expect(admin_page.get_by_text(t("calc.spool.incomplete"))).to_be_visible()

    # The small spool (40 g left) does not hold 100 g: a second spool field appears
    choose_and_wait(admin_page, admin_page.locator("#spool_0_0"), value="2")
    expect(admin_page.locator("#spool_0_1")).to_be_visible()
    expect(admin_page.locator("#res-suggested")).to_have_count(0)

    choose_and_wait(admin_page, admin_page.locator("#spool_0_1"), value="1")
    expect(admin_page.locator("#res-suggested")).to_have_text(re.compile(r"^\d+,\d{2}$"))
    # 40 g x 30 EUR/kg + 60 g x 20 EUR/kg
    expect(admin_page.locator("#res-material")).to_have_text("2,40")
    assert fits_screen_width(admin_page)

    # A change shows at once in the result
    before = admin_page.locator("#res-suggested").inner_text()
    type_and_wait(admin_page, admin_page.locator("#setup_minutes"), "30")
    assert admin_page.locator("#res-suggested").inner_text() != before
    assert fits_screen_width(admin_page)

    # "Material hinzufügen" adds a position without losing what was typed
    with admin_page.expect_response(
        lambda r: r.url.endswith("/calculation/new") and r.request.method == "POST"
    ):
        admin_page.get_by_role("button", name=t("calc.add_position")).click()
    settle(admin_page)
    expect(admin_page.locator("#material_1")).to_be_visible()
    expect(admin_page.locator("#grams_0")).to_have_value("100")
    assert fits_screen_width(admin_page)
