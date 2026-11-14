"""A late "settled" message must not open a closed pop-up again (issue #103, test cases 1 and 2).

HTMX reports "settled" a few milliseconds after it put an answer into the pop-up. If the pop-up
was closed in between (Cancel clicked very fast, as a test on a slow machine does), the message
used to open it again, empty. The message is sent here by hand, so the test does not depend on
timing.
"""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t

LATE_SETTLE = """
() => {
  const body = document.getElementById("popup-body");
  body.dispatchEvent(
    new CustomEvent("htmx:afterSettle", { bubbles: true, detail: { target: body } })
  );
}
"""


def open_popup(page: Page):
    page.goto("/attributes")
    page.get_by_role("button", name=t("attributes.new"), exact=True).click()
    dialog = page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    return dialog


def test_a_late_message_after_an_error_and_cancel_keeps_the_popup_closed(
    admin_page: Page,
) -> None:
    dialog = open_popup(admin_page)
    dialog.locator("button[type=submit]").click()
    expect(dialog.locator("[aria-invalid=true]").first).to_be_visible()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()

    admin_page.evaluate(LATE_SETTLE)

    expect(dialog).not_to_be_visible()
    # The page still works: the same button opens the pop-up with its form again.
    admin_page.get_by_role("button", name=t("attributes.new"), exact=True).click()
    expect(dialog).to_be_visible()
    expect(dialog.locator("#name")).to_be_visible()


def test_a_late_message_keeps_a_closed_popup_closed(admin_page: Page) -> None:
    dialog = open_popup(admin_page)
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()

    admin_page.evaluate(LATE_SETTLE)

    expect(dialog).not_to_be_visible()
