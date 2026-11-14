"""Resetting two-factor login in a real browser, on desktop and phone (issue #39, test case 17)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import LiveServer, t
from tests.browser.test_two_factor import add_two_factor_user, fits_screen_width

RESET_BUTTON = f"button[type=submit]:has-text('{t('user.two_factor.reset.submit')}')"


def test_admin_resets_two_factor_after_confirming(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    # This test failed now and then in GitHub Actions without a cause (issue #103): the reset
    # had already happened after the first click, which was meant to be cancelled. To see what
    # really happened next time, the page's questions, requests and messages are recorded and
    # added to the failure message.
    log: list[str] = []
    admin_page.on(
        "request",
        lambda r: log.append(f"request {r.method} {r.url}") if r.method != "GET" else None,
    )
    admin_page.on("dialog", lambda d: log.append(f"question: {d.message}"))
    admin_page.on("console", lambda m: log.append(f"console {m.type}: {m.text}"))
    admin_page.on("pageerror", lambda e: log.append(f"page error: {e}"))
    try:
        reset_after_confirming(admin_page, live_server, viewport_name, log)
    except Exception as error:
        raise AssertionError(f"{error}\n\nRecorded in the browser:\n" + "\n".join(log)) from error


def reset_after_confirming(
    admin_page: Page, live_server: LiveServer, viewport_name: str, log: list[str]
) -> None:
    username = f"handy-weg-{viewport_name}"
    add_two_factor_user(live_server, username)

    admin_page.goto("/settings/users")
    assert fits_screen_width(admin_page)
    row = admin_page.locator("tr", has=admin_page.get_by_role("link", name=username, exact=True))
    expect(row.locator("td").last).to_have_text(t("user.two_factor.on"))
    row.get_by_role("link", name=username, exact=True).click()

    expect(admin_page.locator("h1")).to_have_text(username)
    assert fits_screen_width(admin_page)
    expect(admin_page.locator(RESET_BUTTON)).to_be_visible()

    # "Cancel" in the question keeps everything as it was.
    questions = []

    def answer(accept: bool):
        def handle(dialog) -> None:
            questions.append(dialog.message)
            log.append(f"question: {dialog.message} -> {'accept' if accept else 'dismiss'}")
            dialog.accept() if accept else dialog.dismiss()

        return handle

    log.append(f"before first click: ready={admin_page.evaluate('document.readyState')}")
    admin_page.once("dialog", answer(accept=False))
    admin_page.click(RESET_BUTTON)
    log.append(f"after first click: {admin_page.url}")
    admin_page.reload()
    expect(admin_page.get_by_text(t("user.two_factor.status.on"), exact=True)).to_be_visible()

    admin_page.once("dialog", answer(accept=True))
    admin_page.click(RESET_BUTTON)
    expect(admin_page.locator("[role=status]")).to_have_text(t("user.two_factor.reset.done"))
    expect(admin_page.get_by_text(t("user.two_factor.status.off"), exact=True)).to_be_visible()
    expect(admin_page.locator(RESET_BUTTON)).to_have_count(0)
    assert fits_screen_width(admin_page)
    assert questions == [t("user.two_factor.reset.confirm")] * 2
