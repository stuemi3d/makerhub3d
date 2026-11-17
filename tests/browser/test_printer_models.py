"""Printer models in a real browser, on desktop and phone (issue #177).

A model can only be created from Bambuddy, which the test app does not have; so the test puts
a model straight into the database of the test app and works with it in the pop-up.
"""

import sqlite3

from playwright.sync_api import Locator, Page, expect

from app.config import DATABASE_FILE
from tests.browser.conftest import LiveServer, t
from tests.browser.test_pages import fits_screen_width


def seed_model(live_server: LiveServer, name: str, materials: list[str]) -> None:
    with sqlite3.connect(live_server.data_dir / DATABASE_FILE) as connection:
        cursor = connection.execute(
            "INSERT INTO printer_models (name, created_at) VALUES (?, '2026-11-17 10:00:00')",
            (name,),
        )
        for position, material in enumerate(materials):
            connection.execute(
                "INSERT INTO printer_model_powers (model_id, material, kwh_per_hour, position)"
                " VALUES (?, ?, '0.1', ?)",
                (cursor.lastrowid, material, position),
            )


def open_edit(page: Page, name: str) -> Locator:
    page.goto("/settings/devices")
    page.get_by_role("link", name=f"{t('table.action.edit')} {name}", exact=True).click()
    dialog = page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    return dialog


def materials(dialog: Locator) -> list[str]:
    return dialog.locator("[data-power-row] input[name^=material_]").evaluate_all(
        "(inputs) => inputs.map((input) => input.value)"
    )


def expect_overview(page: Page, name: str, expected: list[str]) -> None:
    """The overview is refreshed after the pop-up closed, so this waits for the new content."""
    cells = page.locator(f'tr[data-model-row="{name}"] td[data-col=material]')
    expect(cells).to_have_text(expected)


def test_rows_are_added_moved_removed_and_saved(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    name = f"P2S-{viewport_name}"
    seed_model(live_server, name, ["PLA", "PETG", "TPU"])
    dialog = open_edit(admin_page, name)
    assert fits_screen_width(admin_page)
    rows = dialog.locator("[data-power-row]")
    assert materials(dialog)[:3] == ["PLA", "PETG", "TPU"]

    # "Material hinzufügen" adds rows without limit (more than the three of before).
    before = rows.count()
    for _ in range(5):
        dialog.locator("[data-power-add]").click()
    expect(rows).to_have_count(before + 5)
    assert fits_screen_width(admin_page)

    # The last row gets PLA-CF and moves with the arrow up to the place below PLA.
    last = rows.last
    last.locator("input[name^=material_]").fill("PLA-CF")
    last.locator("input[name^=kwh_]").fill("0,12")
    # Move step by step until it stands in the second place.
    while materials(dialog).index("PLA-CF") > 1:
        position = materials(dialog).index("PLA-CF")
        rows.nth(position).locator("[data-power-up]").click()
    assert materials(dialog)[:4] == ["PLA", "PLA-CF", "PETG", "TPU"]

    # Removing TPU takes its row away.
    position = materials(dialog).index("TPU")
    rows.nth(position).locator("[data-power-remove]").click()
    assert "TPU" not in materials(dialog)

    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect_overview(admin_page, name, ["PLA", "PLA-CF", "PETG"])
    assert fits_screen_width(admin_page)

    # The order is kept when the pop-up is opened again.
    dialog = open_edit(admin_page, name)
    assert materials(dialog)[:3] == ["PLA", "PLA-CF", "PETG"]


def test_a_row_is_dragged_by_its_handle(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    name = f"H2C-{viewport_name}"
    seed_model(live_server, name, ["A", "B", "C"])
    dialog = open_edit(admin_page, name)
    rows = dialog.locator("[data-power-row]")
    handle = rows.nth(2).locator("[data-power-handle]")
    target = rows.nth(0).bounding_box()
    box = handle.bounding_box()
    assert box is not None and target is not None

    admin_page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    admin_page.mouse.down()
    admin_page.mouse.move(box["x"] + 5, target["y"] + 2, steps=10)
    admin_page.mouse.up()

    assert materials(dialog)[:3] == ["C", "A", "B"]
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect_overview(admin_page, name, ["C", "A", "B"])


def test_a_pair_stays_side_by_side_on_every_screen(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    name = f"X1E-{viewport_name}"
    seed_model(live_server, name, ["PLA", "PETG"])
    dialog = open_edit(admin_page, name)
    row = dialog.locator("[data-power-row]").first
    material = row.locator("input[name^=material_]").bounding_box()
    kwh = row.locator("input[name^=kwh_]").bounding_box()
    assert material is not None and kwh is not None

    # Same line (also on the phone in portrait), material wider than the number.
    assert abs(material["y"] - kwh["y"]) < 5
    assert kwh["x"] > material["x"] + material["width"] - 1
    assert material["width"] > kwh["width"]
    # The column heads stand once above the rows.
    expect(dialog.get_by_text(t("model.power.kwh"), exact=True)).to_have_count(1)
    assert fits_screen_width(admin_page)
    # All four controls of the row are inside the pop-up.
    pop = dialog.bounding_box()
    assert pop is not None
    for control in ("handle", "up", "down", "remove"):
        control_box = row.locator(f"[data-power-{control}]").bounding_box()
        assert control_box is not None
        assert control_box["x"] + control_box["width"] <= pop["x"] + pop["width"] + 1


def test_overview_has_one_row_per_material(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    name = f"A1-{viewport_name}"
    seed_model(live_server, name, ["PLA", "PETG", "TPU"])
    admin_page.goto("/settings/devices")

    rows = admin_page.locator(f'tr[data-model-row="{name}"]')
    expect(rows).to_have_count(3)
    expect_overview(admin_page, name, ["PLA", "PETG", "TPU"])
    # The model name and its actions stand once, in the first row.
    expect(rows.first.locator("td.row-actions")).to_have_count(1)
    expect(rows.nth(1).locator("td.row-actions")).to_have_count(0)
    assert fits_screen_width(admin_page)
