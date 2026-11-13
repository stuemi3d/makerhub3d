"""Existing pages in a real browser, on desktop and on a narrow phone screen."""

import re

import pytest
from playwright.sync_api import Error, Page, expect

from tests.browser.conftest import ADMIN_NAME, LiveServer, t

LOGIN_BUTTON = f"button[type=submit]:has-text('{t('login.submit')}')"
PROTECTED_PATHS = ["/", "/settings", "/settings/users", "/settings/log", "/account"]
# Pages that must fit the screen width: login and the main pages.
FIT_PATHS = ["/settings", "/settings/users"]


# --- Test case 1: login ----------------------------------------------------------------


def test_login_with_right_password_shows_start_page(admin_page: Page) -> None:
    expect(admin_page.locator("h1")).to_have_text(t("nav.dashboard"))
    expect(admin_page.locator("header nav")).to_be_visible()


def test_login_with_wrong_password_stays_on_login_with_message(login_page: Page) -> None:
    login_page.fill("#username", ADMIN_NAME)
    login_page.fill("#password", "das-ist-falsch-123")
    login_page.click(LOGIN_BUTTON)

    expect(login_page.locator("[role=alert]")).to_contain_text(t("login.failed")[:20])
    expect(login_page.locator("h1")).to_have_text(t("login.title"))


# --- Test case 2: all existing pages ---------------------------------------------------


def test_all_pages_open_through_the_navigation(admin_page: Page) -> None:
    steps = [
        (t("nav.settings"), "/settings", t("settings.title")),
        (t("settings.tab.users"), "/settings/users", t("users.title")),
        (t("settings.tab.log"), "/settings/log", t("log.title")),
        (ADMIN_NAME, "/account", t("account.title")),
        (t("nav.dashboard"), "/", t("nav.dashboard")),
    ]
    for link_text, path, heading in steps:
        admin_page.locator("header, main").get_by_role(
            "link", name=link_text, exact=True
        ).first.click()
        expect(admin_page).to_have_url(re.compile(f"{re.escape(path)}$"))
        expect(admin_page.locator("h1")).to_have_text(heading)


# --- Test case 3: settings -------------------------------------------------------------


def test_changed_setting_is_saved_and_still_there_after_reload(
    admin_page: Page, viewport_name: str
) -> None:
    # The test app is shared, so each view uses its own value: a value left over from the
    # other view must not make a broken save look fine.
    new_value = {"desktop": "0,3456", "phone": "0,4567"}[viewport_name]
    admin_page.goto("/settings")
    assert admin_page.input_value("#electricity_price") != new_value
    admin_page.fill("#electricity_price", new_value)
    admin_page.click(f"button[type=submit]:has-text('{t('settings.save')}')")
    expect(admin_page.locator("[role=status]")).to_have_text(t("settings.saved"))

    admin_page.goto("/settings")
    expect(admin_page.locator("#electricity_price")).to_have_value(new_value)


# --- Test case 4: protected pages ------------------------------------------------------


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_logged_out_visitor_is_sent_to_login(page: Page, path: str) -> None:
    page.goto(path)

    expect(page).to_have_url(re.compile(r"/login"))
    expect(page.locator("h1")).to_have_text(t("login.title"))


# --- Test cases 5 and 6: phone view ----------------------------------------------------


def fits_screen_width(page: Page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_login_page_fits_the_screen_width(login_page: Page) -> None:
    assert fits_screen_width(login_page)


@pytest.mark.parametrize("path", ["/", *FIT_PATHS])
def test_pages_fit_the_screen_width(admin_page: Page, path: str) -> None:
    admin_page.goto(path)

    assert fits_screen_width(admin_page)


def test_every_navigation_entry_is_visible_on_screen(admin_page: Page) -> None:
    width = admin_page.viewport_size["width"]
    entries = [t(key) for key in ("nav.dashboard", "nav.articles", "nav.calculation")]
    entries += [t(key) for key in ("nav.production", "nav.stock", "nav.analysis")]
    entries += [t("nav.settings"), ADMIN_NAME, t("nav.logout")]
    for entry in entries:
        item = admin_page.locator("header nav").get_by_text(entry, exact=True)
        expect(item).to_be_visible()
        box = item.bounding_box()
        assert box is not None
        assert box["x"] >= 0 and box["x"] + box["width"] <= width, entry


# --- Test cases 8 and 9: local only, own database ---------------------------------------


def test_browser_cannot_reach_other_addresses(
    page: Page, blocked_requests: list[str], live_server: LiveServer
) -> None:
    with pytest.raises(Error):
        page.goto("http://example.com/")

    assert blocked_requests == ["http://example.com/"]
    blocked_requests.clear()  # expected here, so the safety check does not fail this test


def test_test_app_runs_locally_with_its_own_database(live_server: LiveServer, tmp_path_factory):
    assert live_server.url.startswith("http://127.0.0.1:")
    assert live_server.data_dir.is_relative_to(tmp_path_factory.getbasetemp())
    assert (live_server.data_dir / "makerhub3d.db").exists()
