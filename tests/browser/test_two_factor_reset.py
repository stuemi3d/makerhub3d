"""Resetting two-factor login in a real browser, on desktop and phone (issue #39, test case 17)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import LiveServer, t
from tests.browser.test_two_factor import add_two_factor_user, fits_screen_width

RESET_BUTTON = "a.icon-btn[title^='{}']".format(t("table.action.two_factor"))


def test_admin_resets_two_factor_after_confirming(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    username = f"handy-weg-{viewport_name}"
    add_two_factor_user(live_server, username)

    admin_page.goto("/settings/users")
    assert fits_screen_width(admin_page)
    row = admin_page.locator("tr", has=admin_page.get_by_role("cell", name=username, exact=True))
    expect(row.locator("td").nth(3)).to_have_text(t("user.two_factor.on"))
    assert fits_screen_width(admin_page)
    expect(row.locator(RESET_BUTTON)).to_be_visible()

    # The question is a pop-up of the app, not a browser dialog (issue 169). "Cancel" keeps
    # everything as it was.
    dialog = admin_page.locator("dialog#popup")
    row.locator(RESET_BUTTON).click()
    expect(dialog).to_contain_text(t("user.two_factor.reset.confirm"))
    assert fits_screen_width(admin_page)
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()
    expect(row.locator("td").nth(3)).to_have_text(t("user.two_factor.on"))

    row.locator(RESET_BUTTON).click()
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(row.locator("td").nth(3)).to_have_text(t("user.two_factor.off"))
    expect(row.locator(RESET_BUTTON)).to_have_count(0)
    assert fits_screen_width(admin_page)
