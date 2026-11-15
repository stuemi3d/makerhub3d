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
    # Close the socket too: otherwise later pages wait for an answer that never comes
    # (the test app still has this address saved) instead of being refused at once.
    server.server_close()


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
    expect(admin_page.locator("#open-prints .data-card")).to_have_count(1)
    expect(admin_page.locator("#open-prints tr.open-row")).to_have_count(2)
    expect(admin_page.locator("#open-prints tr.print-failed")).to_have_count(1)
    expect(admin_page.locator("#open-prints .card-head a")).to_be_visible()
    # The card stays inside the screen; at most the table scrolls inside it.
    assert fits_screen_width(admin_page)
    card = admin_page.locator("#open-prints .data-card").bounding_box()
    assert card["x"] >= 0 and card["x"] + card["width"] <= admin_page.viewport_size["width"]


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


def test_booking_and_cancelling_work_on_every_screen(
    admin_page: Page, fake_bambuddy: str, viewport_name: str
) -> None:
    connect(admin_page, fake_bambuddy)
    number = f"3D-P-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Halter")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Halter")

    admin_page.goto("/production")
    admin_page.locator("tr.print-row").first.locator("input[name=prints]").check()
    admin_page.get_by_role("button", name=t("production.create"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("production.new.title"))
    assert fits_screen_width(admin_page)

    admin_page.select_option("#article_1", label=f"{number} · Halter")
    admin_page.fill("#quantity_1", "4")
    admin_page.get_by_role("button", name=t("production.preview"), exact=True).click()
    expect(admin_page.locator(".preview-total")).to_be_visible()
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("button", name=t("production.book"), exact=True).click()
    expect(admin_page.locator("[role=status]")).to_contain_text(t("production.booked"))
    # The browser tests share one database: look for the row of this test's article only.
    row = admin_page.locator("tr.production-row").filter(has_text=number)
    expect(row).to_have_count(1)
    assert fits_screen_width(admin_page)

    row.get_by_role("button", name=t("production.cancel"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("production.cancel.title"))
    admin_page.get_by_role("button", name=t("production.cancel.submit"), exact=True).click()
    expect(admin_page.locator("[role=status]")).to_contain_text(t("production.cancelled_done"))
    expect(admin_page.locator("tr.production-cancelled").filter(has_text=number)).to_have_count(1)
    assert fits_screen_width(admin_page)


def test_the_create_button_waits_for_a_chosen_print(admin_page: Page, fake_bambuddy: str) -> None:
    connect(admin_page, fake_bambuddy)
    admin_page.goto("/production")
    button = admin_page.get_by_role("button", name=t("production.create"), exact=True)
    boxes = admin_page.locator("input[name=prints]")
    assert fits_screen_width(admin_page)

    expect(button).to_be_disabled()
    boxes.nth(0).check()
    expect(button).to_be_enabled()
    expect(admin_page.locator("[data-selected-text]")).to_have_text(f"1 {t('production.selected')}")
    boxes.nth(1).check()
    expect(admin_page.locator("[data-selected-text]")).to_have_text(f"2 {t('production.selected')}")
    boxes.nth(0).uncheck()
    boxes.nth(1).uncheck()
    expect(button).to_be_disabled()
    expect(admin_page.locator("[data-selected-text]")).to_have_text("")
    # The button sits in the head, above the table.
    button_box = button.bounding_box()
    table_box = admin_page.locator("table.data-table").bounding_box()
    assert button_box["y"] < table_box["y"]


SCROLLERS = """
() => [...document.querySelectorAll('main *')].filter((e) => {
  const overflow = getComputedStyle(e).overflowX;
  return (overflow === 'auto' || overflow === 'scroll') && e.scrollWidth > e.clientWidth + 1;
}).length
"""


@pytest.mark.parametrize("width", [390, 600, 1000])
def test_the_form_has_no_sideways_scrolling_and_a_wide_article_choice(
    admin_page: Page, fake_bambuddy: str, width: int, viewport_name: str
) -> None:
    connect(admin_page, fake_bambuddy)
    number = f"3D-W-{width}-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Halter mit langem Namen")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Halter")

    admin_page.set_viewport_size({"width": width, "height": 900})
    admin_page.goto("/production")
    admin_page.locator("input[name=prints]").first.check()
    admin_page.get_by_role("button", name=t("production.create"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("production.new.title"))

    before = admin_page.locator("#article_1").bounding_box()["width"]
    assert admin_page.evaluate(SCROLLERS) == 0
    assert fits_screen_width(admin_page)
    # Wide enough for a number and a name: at least about 12 characters of the text size.
    assert before >= min(width - 60, 170)

    admin_page.select_option("#article_1", label=f"{number} · Halter mit langem Namen")
    admin_page.fill("#quantity_1", "2")
    admin_page.get_by_role("button", name=t("production.preview"), exact=True).click()
    expect(admin_page.locator(".preview-total")).to_be_visible()

    after = admin_page.locator("#article_1").bounding_box()["width"]
    assert admin_page.evaluate(SCROLLERS) == 0
    assert fits_screen_width(admin_page)
    assert abs(after - before) <= 2
