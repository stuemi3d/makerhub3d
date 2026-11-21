"""A changed spool price is easy to see, in a real browser (issue #245, case 24).

Bambuddy is played by the tiny server of the spool test. The price in Bambuddy is changed
before the spool is assigned: the list is first read when the page is opened.
"""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width
from tests.browser.test_production import connect
from tests.browser.test_spool_assignment import SERVED, fake_bambuddy  # noqa: F401


def spool(viewport: str, price: float) -> dict:
    return {
        "id": 81 if viewport == "desktop" else 91,
        "material": "PETG",
        "brand": "Beispielmarke",
        "subtype": "",
        "color_name": f"Hinweis {viewport}",
        "rgba": "778899FF",
        "cost_per_kg": price,
        "label_weight": 1000,
        "weight_used": 100,
        "archived_at": None,
    }


def test_chip_notice_and_overview_show_a_changed_price(
    admin_page: Page,
    fake_bambuddy: str,  # noqa: F811
    viewport_name: str,
) -> None:
    mine = spool(viewport_name, 20)
    SERVED[:] = [spool("desktop", 20), spool("phone", 20)]
    connect(admin_page, fake_bambuddy)
    admin_page.goto("/filaments/spools")
    row = admin_page.locator(f"#spool-{mine['id']}")
    row.get_by_role("button", name=f"{t('filaments.spools.confirm_one')} #{mine['id']}").click()
    expect(row).to_have_count(0)

    # Bambuddy now says 25 instead of 20 (both spools are served, see test_filament_stock).
    SERVED[:] = [spool("desktop", 25), spool("phone", 25)]

    admin_page.goto("/filaments")
    line = admin_page.locator("table.data-table tbody tr").filter(has_text=mine["color_name"])
    expect(line.locator("[data-price-chip]")).to_have_text(
        t("filaments.price.chip").replace("{n}", "1")
    )
    notice = admin_page.locator("[data-price-notice]")
    expect(notice).to_be_visible()
    assert fits_screen_width(admin_page)

    notice.get_by_role("link", name=t("filaments.price.notice.link")).click()
    changed = admin_page.locator(f"#change-{mine['id']}")
    expect(changed).to_contain_text("25,00")
    assert fits_screen_width(admin_page)
    admin_page.wait_for_load_state("load")  # the scripts must be ready before the pop-up link

    changed.get_by_role("link", name=t("table.action.take")).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(admin_page.locator(f"#change-{mine['id']}")).to_have_count(0)
