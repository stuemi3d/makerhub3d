"""The spool details of a filament in a real browser, on desktop and phone (issue #248, case 9).

Bambuddy is played by the tiny server of the spool test.
"""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width
from tests.browser.test_production import connect
from tests.browser.test_spool_assignment import SERVED, fake_bambuddy  # noqa: F401
from tests.browser.test_spool_follow import spools


def test_spool_details_fit_the_screen_and_actions_stay_reachable(
    admin_page: Page,
    fake_bambuddy: str,  # noqa: F811
    viewport_name: str,
) -> None:
    mine = spools(viewport_name)[0]
    # own spool numbers (101/111): the browser database is shared with the other spool tests
    mine["id"] = 101 if viewport_name == "desktop" else 111
    mine["color_name"] = f"Details {viewport_name}"
    mine["note"] = "Eine ziemlich lange Notiz zur Spule, " * 6
    mine["last_dried_at"] = "2026-10-01T08:00:00"
    SERVED[:] = [mine]
    connect(admin_page, fake_bambuddy)

    admin_page.goto("/filaments/spools")
    row = admin_page.locator(f"#spool-{mine['id']}")
    row.get_by_role("button", name=f"{t('filaments.spools.confirm_one')} #{mine['id']}").click()
    expect(row).to_have_count(0)

    admin_page.goto("/filaments")
    line = admin_page.locator("table.data-table tbody tr").filter(has_text=mine["color_name"])
    line.locator(".cell-spools a").click()
    assigned = admin_page.locator(f"#assigned-{mine['id']}")
    expect(assigned.locator(".cell-remaining")).to_have_text("900")
    expect(assigned.locator(".cell-dried")).to_have_text("01.10.2026")
    expect(assigned.locator(".cell-note")).to_contain_text("lange Notiz")
    assert fits_screen_width(admin_page)
    admin_page.wait_for_load_state("load")
    expect(assigned.get_by_role("link", name=t("table.action.release"))).to_be_visible()
