"""Settings pages in a real browser, on desktop and phone (issue #91, test case 9)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def test_settings_pages_work_on_every_screen(admin_page: Page, viewport_name: str) -> None:
    name = f"anna-{viewport_name}"
    admin_page.goto("/settings/users")
    expect(admin_page.locator("h1")).to_have_text(t("users.title"))
    expect(admin_page.locator("table.data-table .chip-active").first).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("button", name=t("users.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    # An empty form keeps the pop-up open with errors; Cancel closes it without saving.
    dialog.locator("button[type=submit]").click()
    expect(dialog.locator("[aria-invalid=true]").first).to_be_visible()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()

    admin_page.get_by_role("button", name=t("users.new"), exact=True).click()
    admin_page.fill("#username", name)
    admin_page.fill("#password", "ein-langes-start-passwort")
    admin_page.fill("#password_repeat", "ein-langes-start-passwort")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(admin_page.get_by_text(name, exact=True)).to_be_visible()
    assert fits_screen_width(admin_page)

    for path in ("/settings/backups", "/settings/log", "/settings/log/logins", "/settings"):
        admin_page.goto(path)
        assert fits_screen_width(admin_page), path
    admin_page.goto("/settings/appearance")
    assert fits_screen_width(admin_page)
