"""Bambuddy status on desktop and phone (issue #122, test case 6)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def test_status_block_fits_and_shows_both_states(admin_page: Page) -> None:
    admin_page.goto("/settings/bambuddy")
    block = admin_page.locator(".bambuddy-status")
    # The browser tests share one database, so the state before saving is not fixed here
    # (the empty state is covered by tests/test_bambuddy_status.py).
    expect(block).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.fill("#url", "http://bambuddy.example.com:8000")
    admin_page.fill("#api_key", "test-key-nur-fuer-tests")
    admin_page.locator("main form[action='/settings/bambuddy'] button[type=submit]").click()
    expect(block).to_contain_text(t("bambuddy.status.ready"))
    expect(block).to_contain_text("http://bambuddy.example.com:8000")
    expect(admin_page.locator(".settings-tabs .status-dot-on")).to_be_visible()
    assert fits_screen_width(admin_page)
