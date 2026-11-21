"""The page "Filamente" in a real browser, on desktop and phone (issue #235, test case 12)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import open_menu, t
from tests.browser.test_pages import fits_screen_width


def row_of(page: Page, text: str):
    return page.locator("table.data-table tbody tr").filter(has_text=text)


def test_a_filament_is_created_edited_and_switched_off_in_popups(
    admin_page: Page, viewport_name: str
) -> None:
    color = f"Weiß {viewport_name}"
    admin_page.goto("/")
    # The menu entry leads to the page (on the phone the menu is opened first).
    open_menu(admin_page)
    admin_page.get_by_role("link", name=t("nav.filaments"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("filaments.title"))
    assert fits_screen_width(admin_page)

    # An empty form keeps the pop-up open with errors; Cancel closes it without saving.
    admin_page.get_by_role("button", name=t("filaments.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    dialog.locator("button[type=submit]").click()
    expect(dialog.locator("[aria-invalid=true]").first).to_be_visible()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()

    admin_page.get_by_role("button", name=t("filaments.new"), exact=True).click()
    dialog.locator("#brand").fill("Bambu Lab")
    dialog.locator("#material").fill("PLA")
    dialog.locator("#subtype").fill("Matte")
    dialog.locator("#color_name").fill(color)
    dialog.locator("#color").fill("#FFFFF0")
    dialog.locator("#standard_price").fill("19,90")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, color)).to_contain_text("19,90")
    assert fits_screen_width(admin_page)

    # Edit: the name follows the change.
    row_of(admin_page, color).get_by_role("link", name=t("table.action.edit")).click()
    dialog.locator("#standard_price").fill("21,50")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, color)).to_contain_text("21,50")

    # Switching off shows the state in its row.
    row_of(admin_page, color).get_by_role("button", name=t("table.action.deactivate")).click()
    expect(row_of(admin_page, color)).to_contain_text(t("article.status.inactive"))
    assert fits_screen_width(admin_page)
