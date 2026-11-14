"""Top navigation bar in a real browser (issue #74), on desktop and on the phone."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import ADMIN_NAME, t

TOGGLE = "header .nav-toggle"
MENU = "#main-menu"


def is_phone(page: Page) -> bool:
    return page.viewport_size["width"] < 768


# --- Test cases 1 and 2: bar and current entry ------------------------------------------


def test_bar_shows_name_entries_user_and_marks_the_current_entry(admin_page: Page) -> None:
    admin_page.goto("/articles")
    bar = admin_page.locator("header nav")
    expect(bar.get_by_text("MakerHub 3D")).to_be_visible()
    if is_phone(admin_page):
        admin_page.click(TOGGLE)
    expect(bar.get_by_role("link", name=t("nav.articles"), exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    expect(bar.get_by_text(ADMIN_NAME, exact=True)).to_be_visible()
    expect(bar.get_by_role("button", name=t("nav.logout"))).to_be_visible()


# --- Test case 7: full width -------------------------------------------------------------


def test_content_uses_the_full_width(admin_page: Page) -> None:
    admin_page.goto("/articles")
    width = admin_page.viewport_size["width"]
    box = admin_page.locator("main").bounding_box()
    assert box["width"] >= width - 80  # only the page padding is left over


# --- Test cases 8-10: phone menu ---------------------------------------------------------


def test_menu_is_collapsed_on_the_phone_and_open_on_the_desktop(admin_page: Page) -> None:
    if is_phone(admin_page):
        expect(admin_page.locator(TOGGLE)).to_be_visible()
        expect(admin_page.locator(MENU)).to_be_hidden()
    else:
        expect(admin_page.locator(TOGGLE)).to_be_hidden()
        expect(admin_page.locator(MENU)).to_be_visible()


def test_menu_button_opens_and_closes_the_menu(admin_page: Page) -> None:
    if not is_phone(admin_page):
        pytest.skip("the menu button only exists on the phone")
    admin_page.click(TOGGLE)
    expect(admin_page.locator(TOGGLE)).to_have_attribute("aria-expanded", "true")
    expect(admin_page.locator(MENU).get_by_text(t("nav.stock"), exact=True)).to_be_visible()
    admin_page.click(TOGGLE)
    expect(admin_page.locator(TOGGLE)).to_have_attribute("aria-expanded", "false")
    expect(admin_page.locator(MENU)).to_be_hidden()


def test_open_menu_does_not_overflow_the_screen(admin_page: Page) -> None:
    if is_phone(admin_page):
        admin_page.click(TOGGLE)
    assert admin_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


# --- Test case 11: logout ----------------------------------------------------------------


def test_logout_works_from_the_bar(admin_page: Page) -> None:
    if is_phone(admin_page):
        admin_page.click(TOGGLE)
    admin_page.locator("header").get_by_role("button", name=t("nav.logout")).click()
    expect(admin_page).to_have_url(re.compile(r"/login$"))
