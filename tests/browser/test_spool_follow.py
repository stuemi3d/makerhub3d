"""The spools of a filament in a real browser, on desktop and phone (issue #245, browser test).

Bambuddy is played by the tiny server of the spool test. Releasing a spool goes through the
pop-up of the app, never through a browser dialog.
"""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width
from tests.browser.test_production import connect
from tests.browser.test_spool_assignment import SERVED, fake_bambuddy  # noqa: F401


def spools(viewport: str) -> list[dict]:
    base = 61 if viewport == "desktop" else 71
    return [
        {
            "id": base,
            "material": "PETG",
            "brand": "Beispielmarke",
            "subtype": "",
            "color_name": f"Nachfuehren {viewport}",
            "rgba": "445566FF",
            "cost_per_kg": 20,
            "label_weight": 1000,
            "weight_used": 100,
            "archived_at": None,
        }
    ]


def test_spools_of_a_filament_show_state_and_can_be_released(
    admin_page: Page,
    fake_bambuddy: str,  # noqa: F811
    viewport_name: str,
) -> None:
    mine = spools(viewport_name)[0]
    SERVED[:] = spools("desktop") + spools("phone")
    connect(admin_page, fake_bambuddy)

    admin_page.goto("/filaments/spools")
    row = admin_page.locator(f"#spool-{mine['id']}")
    row.get_by_role("button", name=f"{t('filaments.spools.confirm_one')} #{mine['id']}").click()
    expect(row).to_have_count(0)

    admin_page.goto("/filaments")
    line = admin_page.locator("table.data-table tbody tr").filter(has_text=mine["color_name"])
    line.locator(".cell-spools a").click()
    assigned = admin_page.locator(f"#assigned-{mine['id']}")
    expect(assigned.locator(".cell-state")).to_have_text(t("filaments.state.active"))
    assert fits_screen_width(admin_page)
    admin_page.wait_for_load_state("load")  # the scripts must be ready before the pop-up link

    assigned.get_by_role("link", name=t("table.action.release")).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(admin_page.locator(f"#assigned-{mine['id']}")).to_have_count(0)

    admin_page.goto("/filaments/spools")
    expect(admin_page.locator(f"#spool-{mine['id']}")).to_have_count(1)
