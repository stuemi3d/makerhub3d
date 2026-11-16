"""User edit, password reset and attribute values as pop-ups, in a real browser, on desktop and
phone (issue #171, test cases 1, 4, 5 and 7). Names are unique: the browser tests share one
database."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def action(row, kind: str):
    return row.locator(f"a.icon-btn[title^='{t('table.action.' + kind)}']")


def test_user_edit_and_password_in_pop_ups(admin_page: Page, viewport_name: str) -> None:
    name = f"edit-{viewport_name}"
    admin_page.goto("/settings/users/new")
    admin_page.fill("#username", name)
    admin_page.fill("#password", "ein-langes-start-passwort")
    admin_page.fill("#password_repeat", "ein-langes-start-passwort")
    admin_page.locator("main form button[type=submit]").click()

    admin_page.goto("/settings/users")
    row = admin_page.locator("tr", has=admin_page.get_by_role("cell", name=name, exact=True))
    expect(action(row, "edit")).to_be_visible()  # the icons need no sideways scrolling
    assert fits_screen_width(admin_page)
    dialog = admin_page.locator("dialog#popup")

    action(row, "edit").click()
    expect(dialog.locator("h1")).to_have_text(name)
    assert fits_screen_width(admin_page)
    dialog.locator("#role").select_option("read_only")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(row.locator("td").nth(1)).to_have_text(t("role.read_only"))

    action(row, "password").click()
    expect(dialog.locator("h1")).to_have_text(t("user.reset.title"))
    dialog.locator("#password").fill("kurz")
    dialog.locator("#password_repeat").fill("kurz")
    dialog.locator("button[type=submit]").click()
    expect(dialog.locator("[aria-invalid=true]").first).to_be_visible()  # stays open
    assert fits_screen_width(admin_page)
    dialog.locator("#password").fill("ein-anderes-langes-passwort")
    dialog.locator("#password_repeat").fill("ein-anderes-langes-passwort")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()


def test_add_attribute_value_in_a_pop_up(admin_page: Page, viewport_name: str) -> None:
    name = f"Wert-{viewport_name}"
    admin_page.goto("/attributes")
    admin_page.get_by_role("button", name=t("attributes.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    admin_page.fill("#name", name)
    admin_page.fill("#values", "rot = rt")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()

    row = admin_page.locator("tr", has=admin_page.get_by_role("cell", name=name, exact=True))
    expect(action(row, "add_value")).to_be_visible()
    expect(action(row, "delete")).to_be_visible()
    assert fits_screen_width(admin_page)
    action(row, "add_value").click()
    expect(dialog.locator("#value")).to_be_focused()
    assert fits_screen_width(admin_page)
    dialog.locator("#value").fill("rot")
    dialog.locator("#abbreviation").fill("rx")
    dialog.locator("button[type=submit]").click()
    expect(dialog.locator("[role=alert]")).to_be_visible()  # the value exists: stays open
    dialog.locator("#value").fill("blau")
    dialog.locator("#abbreviation").fill("bl")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(row.locator("td").nth(1)).to_contain_text("blau (bl)")

    action(row, "delete").click()
    dialog.get_by_role("button", name=t("attribute.delete.button")).click()
    expect(dialog).not_to_be_visible()
    expect(admin_page.get_by_role("cell", name=name, exact=True)).to_have_count(0)
