"""The parts mask in the production form on desktop and phone (issue #215, test case 7).

Bambuddy is played by a tiny server on 127.0.0.1 that answers with an invented print, its plate
(two parts) and a small invented sliced file; the browser only talks to the test app.
"""

import json
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import HOST, t
from tests.browser.test_pages import fits_screen_width
from tests.browser.test_production import PRINTS, connect, ensure_printer
from tests.test_plate_parts import sliced

PLATES = {
    "archive_id": 3,
    "filename": "beispiel.gcode.3mf",
    "plates": [
        {
            "index": 1,
            "name": "Würfel",
            "objects": ["Würfel", "Zylinder"],
            "object_count": 2,
            "filament_used_grams": 44,
            "filaments": [{"color": "#AA0000", "used_grams": 44}],
        }
    ],
}
FILE = sliced([("Würfel", 10.0, 0), ("Zylinder", 30.0, 0)], shared=4.0)


class FakeBambuddy(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/v1/archives/3/plates":
            body, kind = json.dumps(PLATES).encode(), "application/json"
        elif path == "/api/v1/archives/3/download":
            body, kind = FILE, "application/octet-stream"
        elif path == "/api/v1/archives/":
            offset = (
                int(self.path.split("offset=")[-1].split("&")[0]) if "offset=" in self.path else 0
            )
            body, kind = json.dumps(PRINTS if offset == 0 else []).encode(), "application/json"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
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


def wait_for_redraw(page: Page) -> None:
    page.wait_for_function(
        "() => !document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )


def press(page: Page, label: str) -> None:
    """A button of the mask; the form is drawn again afterwards."""
    with page.expect_response(
        lambda r: r.url.endswith("/production/new") and r.request.method == "POST"
    ):
        page.locator("#popup").get_by_role("button", name=label, exact=True).click()
    wait_for_redraw(page)


def test_the_parts_mask_fits_and_works(admin_page: Page, fake_bambuddy: str) -> None:
    connect(admin_page, fake_bambuddy)
    ensure_printer(admin_page)
    admin_page.goto("/production")
    admin_page.locator("tr.print-row").first.locator("input[name=prints]").check()
    admin_page.get_by_role("button", name=t("production.create"), exact=True).click()
    popup = admin_page.locator("#popup")
    expect(popup.locator("h1")).to_have_text(t("production.new.title"))

    # The two parts of the plate are there, with the mode preselected
    expect(popup.locator("#parts-3 .part-line")).to_have_count(2)
    expect(popup.locator("input[name=parts_mode][value=different]")).to_be_checked()
    assert fits_screen_width(admin_page)
    for line in popup.locator("#parts-3 .part-line").all():
        # Wait until the line is drawn (the mask may still be settling), then measure it
        expect(line).to_be_visible()
        box = line.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= admin_page.viewport_size["width"]

    # Scrap on the first part: the quote of the plate appears (by weight, 11 of 44 g)
    popup.locator("input[name=pwaste_3_0]").fill("1")
    press(admin_page, t("production.parts.apply"))
    expect(popup.locator("#parts-3")).to_contain_text(t("production.parts.quote"))
    expect(popup.locator("#parts-3 .form-block-head")).to_contain_text("25 %")
    expect(popup.locator("#failed_pieces")).to_have_value("1")
    assert fits_screen_width(admin_page)

    # Merging the two parts gives one line with quantity 2
    popup.locator("input[name=ppick_3_0]").check()
    popup.locator("input[name=ppick_3_1]").check()
    press(admin_page, t("production.parts.merge"))
    expect(popup.locator("#parts-3 .part-line")).to_have_count(1)
    expect(popup.locator("input[name=pqty_3_0]")).to_have_value("2")
    assert fits_screen_width(admin_page)

    # Switching the mode and back keeps the page inside the screen
    popup.locator("input[name=parts_mode][value=same]").check()
    wait_for_redraw(admin_page)
    expect(popup.locator("#parts-3 .part-line")).to_have_count(1)
    assert re.search(r"\d", popup.locator("#quantity").input_value())
    assert fits_screen_width(admin_page)
