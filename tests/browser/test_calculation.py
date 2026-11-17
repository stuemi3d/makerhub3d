"""The calculation list and its pop-ups in a real browser, on desktop and phone (issues #161, #186).

Bambuddy is played by a tiny server on 127.0.0.1 that answers with invented spools; the test
app (a separate process) reads from it, the browser only talks to the test app. The printer and
its model are made for the test (the browser tests share one database, so every viewport has
its own names and only changes are checked).
"""

import io
import json
import re
import sqlite3
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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
    # Issue #181: first the choice "with file" or "by hand"
    dialog.get_by_role("button", name=t("calc.start.hand")).click()
    expect(dialog.locator("#calc-form")).to_be_visible()
    settle(page)
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
    first = admin_page.locator("#material_0-combo").bounding_box()
    second = admin_page.locator("#grams_0").bounding_box()
    assert abs(first["y"] - second["y"]) <= 1
    assert fits_screen_width(admin_page)


def test_the_material_and_spool_choices_are_comboboxes_filtered_while_typing(
    admin_page: Page, live_server: LiveServer, fake_bambuddy: str, viewport_name: str
) -> None:
    """Issue #191: typing into the field itself filters its list; the choice goes to the form."""
    printer, _ = prepare(admin_page, live_server, fake_bambuddy, viewport_name, "s")
    admin_page.goto("/calculation")
    dialog = open_new(admin_page)
    choose_and_wait(admin_page, admin_page.locator("#printer"), label=printer)
    material = dialog.locator("input[role=combobox][data-for=material_0]")
    expect(material).to_be_visible()
    assert fits_screen_width(admin_page)

    # The material is chosen by typing, too
    material.click()
    material.fill("pl")
    expect(dialog.locator("#material_0-list li[data-value=PLA]")).to_be_visible()
    material.fill("xyz")
    expect(dialog.locator("#material_0-list li.combo-none")).to_have_text(
        t("calc.search.material_none")
    )
    material.fill("pl")
    with admin_page.expect_response(
        lambda r: r.url.endswith("/calculation/new") and r.request.method == "POST"
    ):
        dialog.locator("#material_0-list li[data-value=PLA]").click()
    settle(admin_page)
    expect(admin_page.locator("#material_0-combo")).to_have_value("PLA")
    expect(admin_page.locator("#material_0")).to_have_value("PLA")
    type_and_wait(admin_page, admin_page.locator("#grams_0"), "100")

    spool = dialog.locator("input[role=combobox][data-for=spool_0_0]")
    expect(spool).to_be_visible()
    # The label belongs to the field you type in
    expect(dialog.get_by_label(t("calc.field.spool")).first).to_have_attribute("role", "combobox")
    # Nothing is sent from the field while typing
    requests: list[str] = []
    admin_page.on("request", lambda r: requests.append(r.url) if r.method == "POST" else None)
    spool.click()

    def shown() -> list[str]:
        return dialog.locator("#spool_0_0-list li[data-value]").evaluate_all(
            "(o) => o.map((x) => x.dataset.value)"
        )

    assert shown() == ["", "1", "2"]
    spool.fill("WEI\u00df")
    admin_page.wait_for_timeout(900)
    assert requests == []
    assert shown() == ["", "1"]
    spool.fill("fast")
    assert shown() == ["", "2"]
    spool.fill("#1 ")
    assert shown() == ["", "1"]
    spool.fill("beispielmarke")
    assert shown() == ["", "1", "2"]
    spool.fill("zzz")
    assert shown() == [""]
    expect(dialog.locator("#spool_0_0-list li.combo-none")).to_have_text(
        t("calc.search.spool_none")
    )

    # Keyboard: typing and Enter takes the first match; Escape only closes the list
    spool.fill("fast")
    spool.press("Escape")
    expect(dialog.locator("#spool_0_0-list")).to_be_hidden()
    expect(dialog).to_be_visible()
    spool.click()
    expect(dialog.locator("#spool_0_0-list")).to_be_visible()
    spool.fill("fast")
    with admin_page.expect_response(
        lambda r: r.url.endswith("/calculation/new") and r.request.method == "POST"
    ):
        spool.press("Enter")
    settle(admin_page)
    expect(admin_page.locator("#spool_0_0")).to_have_value("2")
    expect(admin_page.locator("#spool_0_0-combo")).to_have_value(re.compile(r"Fast leer"))
    # The small spool does not hold 100 g: the second field appears with its own combobox
    second = dialog.locator("input[role=combobox][data-for=spool_0_1]")
    expect(second).to_be_visible()
    second.click()
    second.fill("zzz")
    # Leaving the field without a choice puts the chosen text back
    admin_page.locator("#grams_0").click()
    expect(second).to_have_value("")
    expect(admin_page.locator("#spool_0_0-combo")).to_have_value(re.compile(r"Fast leer"))
    assert fits_screen_width(admin_page)


SLICER = Path(__file__).resolve().parent.parent / "fixtures" / "slicer"


def sliced_file(extra: dict[str, bytes] | None = None) -> bytes:
    """A .gcode.3mf made of the invented test files (two plates)."""
    entries = {
        "Metadata/slice_info.config": (SLICER / "slice_info.config").read_bytes(),
        "Metadata/project_settings.config": (
            SLICER / "project_settings_excerpt.config"
        ).read_bytes(),
        "Metadata/plate_1.gcode": (SLICER / "plate_1_excerpt.gcode").read_bytes(),
    }
    entries.update(extra or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def as_upload(content: bytes, name: str = "wuerfel.gcode.3mf") -> dict:
    return {"name": name, "mimeType": "application/octet-stream", "buffer": content}


def test_with_file_choice_upload_plates_and_prefilled_form(
    admin_page: Page, live_server: LiveServer, fake_bambuddy: str, viewport_name: str
) -> None:
    """Issue #181: the way "with file" in the pop-up, on desktop and phone."""
    printer, _ = prepare(admin_page, live_server, fake_bambuddy, viewport_name, "d")
    admin_page.goto("/calculation")
    admin_page.get_by_role("button", name=t("calc.new")).first.click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog.get_by_role("button", name=t("calc.start.file"))).to_be_visible()
    expect(dialog.get_by_role("button", name=t("calc.start.hand"))).to_be_visible()
    assert fits_screen_width(admin_page)
    dialog.get_by_role("button", name=t("calc.start.file")).click()
    expect(dialog.locator("#file")).to_be_visible()
    assert fits_screen_width(admin_page)

    # A wrong file: a clear hint, the pop-up stays
    dialog.locator("#printer").select_option(label=printer)
    dialog.locator("#file").set_input_files(as_upload(b"just text", "text.gcode.3mf"))
    dialog.get_by_role("button", name=t("calc.file.read")).click()
    expect(dialog.locator("#file-error")).to_have_text(t("calc.file.error.invalid"))
    assert fits_screen_width(admin_page)

    # A good file: the plates, none preselected; without a choice a hint
    dialog.locator("#file").set_input_files(as_upload(sliced_file()))
    dialog.get_by_role("button", name=t("calc.file.read")).click()
    boxes = dialog.locator("input[name=plate]")
    expect(boxes).to_have_count(2)
    assert fits_screen_width(admin_page)
    dialog.get_by_role("button", name=t("calc.file.take")).click()
    expect(dialog.locator("#plate-error")).to_have_text(t("calc.file.error.plate"))

    # Both plates are added up; the form is prefilled, the quantity stays empty
    boxes.nth(0).check()
    boxes.nth(1).check()
    dialog.get_by_role("button", name=t("calc.file.take")).click()
    expect(dialog.locator("#calc-form")).to_be_visible()
    settle(admin_page)
    expect(dialog.locator("#print_minutes")).to_have_value("41,12")
    expect(dialog.locator("#grams_0")).to_have_value("9,36")
    expect(dialog.locator("#grams_1")).to_have_value("4,3")
    expect(dialog.locator("#pieces")).to_have_value("")
    expect(dialog.locator("#file-printer-hint")).to_be_visible()
    assert fits_screen_width(admin_page)

    # It goes on like by hand
    # The material is already chosen from the file
    expect(admin_page.locator("#material_0")).to_have_value("PLA")
    type_and_wait(admin_page, admin_page.locator("#pieces"), "2")
    expect(dialog.locator("#calc-form")).to_be_visible()
    expect(dialog.get_by_text("wuerfel.gcode.3mf")).to_be_visible()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()
