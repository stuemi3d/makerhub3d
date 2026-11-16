"""Devices tab in a real browser, on desktop and phone (issue #157, test case 17)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def row_of(page: Page, text: str):
    return page.locator("table.data-table tbody tr").filter(has_text=text)


def test_devices_are_created_in_popups_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    printer = f"Drucker {viewport_name}"
    dryer = f"Trockner {viewport_name}"
    admin_page.goto("/settings/devices")
    expect(admin_page.locator("h1")).to_have_text(t("device.title"))
    assert fits_screen_width(admin_page)

    # An empty form keeps the pop-up open with errors; Cancel closes it without saving.
    admin_page.get_by_role("button", name=t("device.new.printer"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    dialog.locator("button[type=submit]").click()
    expect(dialog.locator("[aria-invalid=true]").first).to_be_visible()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()

    admin_page.get_by_role("button", name=t("device.new.printer"), exact=True).click()
    dialog.locator("#name").fill(printer)
    dialog.locator("#bought_on").fill("2025-11-09")
    dialog.locator("#price").fill("1000")
    dialog.locator("#years").fill("5")
    dialog.locator("#hours_per_year").fill("1000")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, printer)).to_contain_text("0,2")
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("button", name=t("device.new.dryer"), exact=True).click()
    dialog.locator("#name").fill(dryer)
    dialog.locator("#bought_on").fill("2025-12-17")
    dialog.locator("#price").fill("132,99")
    dialog.locator("#years").fill("5")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, dryer)).to_contain_text("26,60")
    expect(admin_page.get_by_text(t("device.surcharge.title"))).to_be_visible()
    assert fits_screen_width(admin_page)

    # Switching a device off shows the state in its row.
    row_of(admin_page, dryer).get_by_role("button", name=t("device.deactivate")).click()
    expect(row_of(admin_page, dryer)).to_contain_text(t("device.status.inactive"))
    assert fits_screen_width(admin_page)


def test_a_device_is_deleted_after_asking(admin_page: Page, viewport_name: str) -> None:
    """Issue #163: the delete button asks first; the device is gone after OK."""
    dryer = f"Löschtrockner {viewport_name}"
    admin_page.goto("/settings/devices")
    admin_page.get_by_role("button", name=t("device.new.dryer"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    dialog.locator("#name").fill(dryer)
    dialog.locator("#bought_on").fill("2025-12-17")
    dialog.locator("#price").fill("100")
    dialog.locator("#years").fill("5")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    assert fits_screen_width(admin_page)

    # Cancelling the question keeps the device.
    questions: list[str] = []
    admin_page.once(
        "dialog", lambda question: (questions.append(question.message), question.dismiss())
    )
    row_of(admin_page, dryer).get_by_role("button", name=t("device.delete")).click()
    assert questions == [t("device.delete.confirm")]
    expect(row_of(admin_page, dryer)).to_be_visible()

    admin_page.once("dialog", lambda question: question.accept())
    row_of(admin_page, dryer).get_by_role("button", name=t("device.delete")).click()
    expect(row_of(admin_page, dryer)).to_have_count(0)
    assert fits_screen_width(admin_page)
