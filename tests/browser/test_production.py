"""Page Production and the dashboard list on desktop and phone (issue #119, test case 17).

Bambuddy is played by a tiny server on 127.0.0.1 that answers with invented prints; the test
app (a separate process) reads from it, the browser only talks to the test app.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import HOST, t
from tests.browser.test_pages import fits_screen_width

PRINTS = [
    {
        "id": 3,
        "printer_id": 1,
        "print_name": "Beispiel Halter mit sehr langem Namen für die schmale Handy-Ansicht",
        "filename": "halter.gcode.3mf",
        "status": "completed",
        "started_at": "2026-11-15T08:00:00",
        "completed_at": None,
        "print_time_seconds": 3900,
        "actual_time_seconds": 3900,
        "energy_cost": 0.01,
        "wear_cost": 0.25,
        "cost": 0.5,
        "created_at": "2026-11-15T08:00:00",
    },
    {
        "id": 2,
        "printer_id": 1,
        "print_name": "Beispiel Fehldruck",
        "filename": "fehl.gcode.3mf",
        "status": "failed",
        "started_at": "2026-11-14T08:00:00",
        "completed_at": None,
        "print_time_seconds": 600,
        "actual_time_seconds": None,
        "energy_cost": None,
        "wear_cost": None,
        "cost": None,
        "created_at": "2026-11-14T08:00:00",
    },
]


class FakeBambuddy(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        offset = int(self.path.split("offset=")[-1].split("&")[0]) if "offset=" in self.path else 0
        body = json.dumps(PRINTS if offset == 0 else []).encode()
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


def connect(page: Page, address: str) -> None:
    page.goto("/settings/bambuddy")
    page.fill("#url", address)
    page.fill("#api_key", "test-key-nur-fuer-tests")
    page.fill("#prints_from", "2020-01-01")
    page.locator("main form[action='/settings/bambuddy'] button[type=submit]").click()
    expect(page.locator(".bambuddy-status")).to_contain_text(t("bambuddy.status.ready"))


def test_list_and_dashboard_fit_the_screen(admin_page: Page, fake_bambuddy: str) -> None:
    connect(admin_page, fake_bambuddy)

    admin_page.goto("/production")
    expect(admin_page.locator("tr.print-row")).to_have_count(2)
    expect(admin_page.locator("tr.print-failed")).to_have_count(1)
    assert fits_screen_width(admin_page)

    admin_page.goto("/")
    expect(admin_page.locator("#open-prints .open-count")).to_have_text("2")
    assert fits_screen_width(admin_page)


def test_ignoring_works_with_the_buttons(admin_page: Page, fake_bambuddy: str) -> None:
    connect(admin_page, fake_bambuddy)
    admin_page.goto("/production")

    admin_page.locator("tr.print-failed").get_by_role("button", name=t("production.ignore")).click()
    expect(admin_page.locator("tr.print-row")).to_have_count(1)

    admin_page.goto("/production?view=ignored")
    expect(admin_page.locator("tr.print-row")).to_have_count(1)
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("button", name=t("production.unignore")).click()
    expect(admin_page.locator("tr.print-row")).to_have_count(0)
