"""Assigning spools to filaments in a real browser, on desktop and phone (issue #236, case 19).

Bambuddy is played by a tiny server on 127.0.0.1 with invented spools. The browser tests share
one database, so every viewport has its own spools (own numbers and colour names) and the fake
Bambuddy only serves the spools of the test that runs.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import HOST, t
from tests.browser.test_pages import fits_screen_width
from tests.browser.test_production import connect


def spools_for(viewport: str) -> list[dict]:
    base = 21 if viewport == "desktop" else 31
    return [
        {
            "id": base + offset,
            "material": "PLA",
            "brand": "Beispielmarke",
            "subtype": "Matte",
            "color_name": f"Farbe {viewport} {letter}",
            "rgba": "FFFFF0FF",
            "cost_per_kg": 19.9,
            "label_weight": 1000,
            "weight_used": 0,
            "archived_at": None,
        }
        for offset, letter in enumerate("AB")
    ]


# What the fake Bambuddy says right now; every test sets its own spools.
SERVED: list[dict] = []


class FakeBambuddy(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload: object = SERVED if "inventory/spools" in self.path else []
        if "settings" in self.path:
            payload = {"energy_cost_per_kwh": 0.3, "currency": "EUR"}
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://{HOST}:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_spools_are_confirmed_one_by_one_and_all_at_once(
    admin_page: Page, fake_bambuddy: str, viewport_name: str
) -> None:
    first, second = spools_for(viewport_name)
    SERVED[:] = [first, second]
    connect(admin_page, fake_bambuddy)

    # The list page has a button with the number of waiting spools.
    admin_page.goto("/filaments")
    button = admin_page.get_by_role("button", name=t("filaments.assign"))
    expect(button).to_be_visible()
    button.click()
    expect(admin_page.locator("h1")).to_have_text(t("filaments.spools.title"))
    assert fits_screen_width(admin_page)

    # One row is confirmed: it leaves the list, the other stays.
    row_a = admin_page.locator(f"#spool-{first['id']}")
    row_b = admin_page.locator(f"#spool-{second['id']}")
    expect(row_a).to_be_visible()
    expect(row_b).to_be_visible()
    row_a.get_by_role("button", name=f"{t('filaments.spools.confirm_one')} #{first['id']}").click()
    expect(row_a).to_have_count(0)
    expect(row_b).to_be_visible()
    assert fits_screen_width(admin_page)

    # Then all that is left are confirmed at once.
    admin_page.get_by_role("button", name=t("filaments.spools.confirm_all")).click()
    expect(row_b).to_have_count(0)

    admin_page.goto("/filaments")
    for spool in (first, second):
        expect(
            admin_page.locator("table.data-table tbody tr").filter(has_text=spool["color_name"])
        ).to_be_visible()
    assert fits_screen_width(admin_page)
