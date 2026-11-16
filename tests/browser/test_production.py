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
    # The form opens in the pop-up over the list of prints
    expect(admin_page.locator("#popup h1")).to_have_text(t("production.new.title"))
    expect(admin_page.locator("#popup")).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.select_option("#article", label=f"{number} · Halter")
    admin_page.fill("#quantity", "4")
    admin_page.get_by_role("button", name=t("production.preview"), exact=True).click()
    expect(admin_page.locator(".preview-total")).to_be_visible()
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("button", name=t("production.book"), exact=True).click()
    # Booked: the pop-up is gone and the list of productions shows the message
    expect(admin_page.locator("#popup")).not_to_be_visible()
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
() => [...document.querySelectorAll('#popup *')].filter((e) => {
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
    expect(admin_page.locator("#popup h1")).to_have_text(t("production.new.title"))
    expect(admin_page.locator("#popup")).to_be_visible()

    before = admin_page.locator("#article").bounding_box()["width"]
    assert admin_page.evaluate(SCROLLERS) == 0
    assert fits_screen_width(admin_page)
    # Wide enough for a number and a name: at least about 12 characters of the text size.
    assert before >= min(width - 60, 170)

    admin_page.select_option("#article", label=f"{number} · Halter mit langem Namen")
    admin_page.fill("#quantity", "2")
    admin_page.get_by_role("button", name=t("production.preview"), exact=True).click()
    expect(admin_page.locator(".preview-total")).to_be_visible()

    after = admin_page.locator("#article").bounding_box()["width"]
    assert admin_page.evaluate(SCROLLERS) == 0
    assert fits_screen_width(admin_page)
    assert abs(after - before) <= 2


def test_the_popup_keeps_the_ticks_when_cancelled(admin_page: Page, fake_bambuddy: str) -> None:
    connect(admin_page, fake_bambuddy)
    admin_page.goto("/production")
    admin_page.locator("input[name=prints]").first.check()
    admin_page.get_by_role("button", name=t("production.create"), exact=True).click()
    expect(admin_page.locator("#popup h1")).to_have_text(t("production.new.title"))

    admin_page.locator("#popup").get_by_role("button", name=t("article.cancel"), exact=True).click()

    expect(admin_page.locator("#popup")).not_to_be_visible()
    expect(admin_page.locator("input[name=prints]").first).to_be_checked()
    expect(admin_page.locator("[data-selected-text]")).to_have_text(f"1 {t('production.selected')}")


def click_and_wait(page: Page, button) -> None:
    """Clicks a button of the pop-up form and waits until the answer is swapped in, so that
    typing on goes into the new form and not into one that is about to be replaced."""
    with page.expect_response(
        lambda r: r.url.endswith("/production/new") and r.request.method == "POST"
    ):
        button.click()
    page.wait_for_function(
        "() => !document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )


def test_the_preview_keeps_working_after_a_mistake_and_changes_the_cost_per_piece(
    admin_page: Page, fake_bambuddy: str, viewport_name: str
) -> None:
    connect(admin_page, fake_bambuddy)
    number = f"PRD-ONE-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Halter")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Halter")
    admin_page.goto("/production")
    admin_page.locator("input[name=prints]").first.check()
    admin_page.get_by_role("button", name=t("production.create"), exact=True).click()
    popup = admin_page.locator("#popup")
    preview = popup.get_by_role("button", name=t("production.preview"), exact=True)
    total = popup.locator(".preview-total")
    per_piece = popup.locator("output.field-static")

    # One article and its good pieces: no extra rows, no weight
    expect(popup.locator("#article")).to_have_count(1)
    expect(popup.locator("[name^=weight]")).to_have_count(0)
    expect(popup.locator("button[value=add_row]")).to_have_count(0)

    popup.locator("#article").select_option(label=f"{number} · Halter")
    popup.locator("#quantity").fill("2")
    click_and_wait(admin_page, preview)
    expect(total).to_be_visible()
    first = per_piece.inner_text()
    # A mistake gives a message and no costs ...
    popup.locator("#quantity").fill("0")
    click_and_wait(admin_page, preview)
    expect(popup.locator("#quantity-error")).to_be_visible()
    expect(total).to_have_count(0)
    # ... and the next try works again; more good pieces make each piece cheaper
    popup.locator("#quantity").fill("4")
    click_and_wait(admin_page, preview)
    expect(total).to_be_visible()
    expect(popup.locator("#quantity-error")).to_have_count(0)
    assert per_piece.inner_text() != first
    expect(popup.locator("#quantity")).to_have_value("4")
    assert fits_screen_width(admin_page)


def test_the_labour_fields_fit_and_the_preview_adds_labour_costs(
    admin_page: Page, fake_bambuddy: str, viewport_name: str
) -> None:
    """Issue #134, case 10: time and rate are in the form, the preview splits the costs, and
    nothing scrolls sideways."""
    connect(admin_page, fake_bambuddy)
    number = f"PRD-LAB-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Halter")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Halter")
    admin_page.goto("/production")
    admin_page.locator("input[name=prints]").first.check()
    admin_page.get_by_role("button", name=t("production.create"), exact=True).click()
    popup = admin_page.locator("#popup")

    expect(popup.locator("#labor_minutes")).to_have_value("")
    expect(popup.locator("#labor_rate")).not_to_have_value("")
    assert admin_page.evaluate(SCROLLERS) == 0
    assert fits_screen_width(admin_page)

    popup.locator("#article").select_option(label=f"{number} · Halter")
    popup.locator("#quantity").fill("2")
    popup.locator("#labor_minutes").fill("60")
    popup.locator("#labor_rate").fill("30")
    click_and_wait(
        admin_page, popup.get_by_role("button", name=t("production.preview"), exact=True)
    )
    expect(popup.locator(".preview-total")).to_contain_text(t("production.preview.labor"))
    expect(popup.locator(".preview-total")).to_contain_text("30")
    expect(popup.locator("#labor_minutes")).to_have_value("60")
    assert admin_page.evaluate(SCROLLERS) == 0
    assert fits_screen_width(admin_page)


def test_the_machine_field_uses_the_rate_from_the_settings(
    admin_page: Page, fake_bambuddy: str, viewport_name: str
) -> None:
    """Issue #135, case 10: rate in the settings, prefilled machine costs (1 h 5 min x 12 EUR
    = 13 EUR), hint when the rate is 0, and nothing scrolls sideways."""
    connect(admin_page, fake_bambuddy)
    # The database is shared by both screen sizes: start from rate 0
    admin_page.goto("/settings")
    admin_page.fill("#machine_rate", "0")
    admin_page.locator("main form[action='/settings'] button[type=submit]").click()
    expect(admin_page.locator("#machine_rate")).to_have_value("0")
    admin_page.goto("/production")
    admin_page.locator("input[name=prints]").first.check()
    admin_page.get_by_role("button", name=t("production.create"), exact=True).click()
    popup = admin_page.locator("#popup")
    expect(popup.locator("input[name=cost_machine_3]")).to_have_value("")
    expect(popup).to_contain_text(t("production.machine_missing.rate"))

    admin_page.goto("/settings")
    admin_page.fill("#machine_rate", "12")
    admin_page.locator("main form[action='/settings'] button[type=submit]").click()
    expect(admin_page.locator("#machine_rate")).to_have_value("12")
    assert fits_screen_width(admin_page)

    admin_page.goto("/production")
    admin_page.locator("input[name=prints]").first.check()
    admin_page.get_by_role("button", name=t("production.create"), exact=True).click()
    popup = admin_page.locator("#popup")
    expect(popup.locator("input[name=cost_machine_3]")).to_have_value("13")
    expect(popup).to_contain_text("1 h 5 min")
    assert admin_page.evaluate(SCROLLERS) == 0
    assert fits_screen_width(admin_page)


def test_hints_do_not_overlap_the_fields_and_the_buttons_stay_visible(
    admin_page: Page, fake_bambuddy: str, viewport_name: str
) -> None:
    """Issue #146, cases 5, 7 and 10: a hint under a field starts below the field, the
    buttons are on the screen without scrolling, and nothing scrolls sideways."""
    connect(admin_page, fake_bambuddy)
    admin_page.goto("/settings")
    admin_page.fill("#machine_rate", "0")
    admin_page.locator("main form[action='/settings'] button[type=submit]").click()
    expect(admin_page.locator("#machine_rate")).to_have_value("0")
    admin_page.goto("/production")
    for box in admin_page.locator("input[name=prints]").all():
        box.check()
    admin_page.get_by_role("button", name=t("production.create"), exact=True).click()
    popup = admin_page.locator("#popup")
    expect(popup.locator("#article")).to_be_visible()

    field = popup.locator("#cost_machine_3")
    hint = popup.locator("small", has_text=t("production.machine_missing.rate")).first
    expect(hint).to_be_visible()
    assert hint.bounding_box()["y"] >= field.bounding_box()["y"] + field.bounding_box()["height"]

    height = admin_page.evaluate("window.innerHeight")
    for name in ("production.preview", "production.book"):
        button = popup.get_by_role("button", name=t(name), exact=True)
        expect(button).to_be_visible()
        box = button.bounding_box()
        assert box["y"] >= 0 and box["y"] + box["height"] <= height
    assert admin_page.evaluate(SCROLLERS) == 0
    assert fits_screen_width(admin_page)
