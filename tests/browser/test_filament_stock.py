"""Stock per filament in a real browser, on desktop and phone (issue #238, browser test).

Bambuddy is played by the tiny server of the spool test. The cache keeps the spool list for
90 seconds across the viewports, so the fake Bambuddy always serves the spools of both.
"""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.numbers import spool_number
from tests.browser.test_pages import fits_screen_width
from tests.browser.test_production import connect
from tests.browser.test_spool_assignment import fake_bambuddy, serve  # noqa: F401


def spools(viewport: str) -> list[dict]:
    return [
        {
            "id": spool_number(__name__, viewport),
            "material": "PETG",
            "brand": "Beispielmarke",
            "subtype": "",
            "color_name": f"Bestand {viewport}",
            "rgba": "112233FF",
            "cost_per_kg": 20,
            "label_weight": 1000,
            "weight_used": 600,
            "archived_at": None,
        }
    ]


def test_stock_and_warning_show_in_the_table(
    admin_page: Page,
    fake_bambuddy: str,  # noqa: F811
    viewport_name: str,
) -> None:
    mine = spools(viewport_name)[0]
    serve(spools("desktop") + spools("phone"))
    connect(admin_page, fake_bambuddy)

    admin_page.goto("/filaments/spools")
    row = admin_page.locator(f"#spool-{mine['id']}")
    row.get_by_role("button", name=f"{t('filaments.spools.confirm_one')} #{mine['id']}").click()
    expect(row).to_have_count(0)

    # Set a minimum of 5 kg in the pop-up: 0,40 kg are below.
    admin_page.goto("/filaments")
    line = admin_page.locator("table.data-table tbody tr").filter(has_text=mine["color_name"])
    line.get_by_role("link", name=t("table.action.edit")).click()
    dialog = admin_page.locator("dialog#popup")
    dialog.locator("#min_stock_kg").fill("5")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()

    line = admin_page.locator("table.data-table tbody tr").filter(has_text=mine["color_name"])
    expect(line.locator(".cell-stock")).to_contain_text("0,40")
    expect(line.locator(".cell-stock .chip-warn")).to_have_text(t("stock.low"))
    expect(line.locator(".cell-spools")).to_have_text("1")
    expect(line.locator(".cell-avg")).to_have_text("20,00")
    expect(line.locator(".cell-last")).to_have_text("20,00")
    assert fits_screen_width(admin_page)
