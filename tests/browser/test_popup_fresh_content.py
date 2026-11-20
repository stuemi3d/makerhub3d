"""New page parts work at once (issue #103, test cases 1 and 2 of the comment on "Fund 3").

HTMX used to wire up new links and forms 20 ms after it put them in. A click in that moment was a
normal link click or form submission: the plain page opened instead of the pop-up. A test on a slow
machine hits that window; here the click comes from a timer of 0 ms right after the swap, so the
test does not depend on the speed of the machine.
"""

import re

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_style_alignment import action

# Reload the list like the app does after saving, and click the "add value" icon of the new list
# in the first moment after the swap.
CLICK_AFTER_REFRESH = """
(title) => new Promise((resolve) => {
  const region = document.querySelector("[data-popup-refresh]");
  document.body.addEventListener("htmx:afterSwap", () => {
    setTimeout(() => {
      document.querySelector(`#attributes-list a.icon-btn[title^='${title}']`).click();
      resolve();
    }, 0);
  }, { once: true });
  window.htmx.ajax("GET", window.location.href, {
    target: region, swap: "outerHTML", select: `#${region.id}`,
  });
})
"""

# Send the form of the pop-up in the first moment after it was put in.
SUBMIT_AFTER_OPEN = """
() => {
  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target.id !== "popup-body") return;
    setTimeout(() => document.querySelector("#popup-body button[type=submit]").click(), 0);
  }, { once: true });
}
"""


def on_list(page: Page) -> None:
    expect(page).to_have_url(re.compile(r"/attributes$"))


def test_1_a_click_right_after_the_list_was_reloaded_opens_the_popup(
    admin_page: Page, viewport_name: str
) -> None:
    name = f"Frisch-{viewport_name}"
    admin_page.goto("/attributes")
    admin_page.get_by_role("button", name=t("attributes.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    admin_page.fill("#name", name)
    admin_page.fill("#values", "rot = rt")
    dialog.locator("button[type=submit]").click()
    expect(dialog).not_to_be_visible()
    row = admin_page.locator("tr", has=admin_page.get_by_role("cell", name=name, exact=True))
    expect(action(row, "add_value")).to_be_visible()

    admin_page.evaluate(CLICK_AFTER_REFRESH, t("table.action.add_value"))

    expect(dialog.locator("#value")).to_be_visible()
    on_list(admin_page)


def test_2_a_form_sent_right_after_the_popup_opened_stays_in_the_popup(
    admin_page: Page,
) -> None:
    admin_page.goto("/attributes")
    admin_page.evaluate(SUBMIT_AFTER_OPEN)
    admin_page.get_by_role("button", name=t("attributes.new"), exact=True).click()

    dialog = admin_page.locator("dialog#popup")
    expect(dialog.locator("[aria-invalid=true]").first).to_be_visible()
    on_list(admin_page)
