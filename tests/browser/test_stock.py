"""Stock in a real browser, on desktop and phone (issue #11, test case 21)."""

import re

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def test_book_withdrawal_and_cancel_it_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-L-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Kabelhalter")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Kabelhalter")

    admin_page.locator("header nav").get_by_text(t("nav.stock"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("stock.title"))
    row = admin_page.locator("tr", has_text=number)
    expect(row).to_contain_text("0")
    assert fits_screen_width(admin_page)

    # First put pieces on stock with a correction, then take some away.
    admin_page.get_by_role("link", name=t("stock.new"), exact=True).click()
    assert fits_screen_width(admin_page)
    admin_page.select_option("#article_id", label=re.compile(number))
    admin_page.select_option("#type", "correction")
    admin_page.fill("#quantity", "10")
    admin_page.fill("#reason", "Inventur")
    admin_page.locator(f"button[type=submit]:has-text('{t('stock.book')}')").click()
    expect(admin_page.locator("tr", has_text=number)).to_contain_text("10")

    admin_page.get_by_role("link", name=t("stock.new"), exact=True).click()
    admin_page.select_option("#article_id", label=re.compile(number))
    admin_page.select_option("#type", "withdrawal")
    admin_page.fill("#quantity", "3")
    admin_page.fill("#reason", "Muster verschenkt")
    admin_page.locator(f"button[type=submit]:has-text('{t('stock.book')}')").click()
    expect(admin_page.locator("tr", has_text=number)).to_contain_text("7")

    admin_page.get_by_role("link", name=t("stock.movements.title"), exact=True).first.click()
    expect(admin_page.locator("h1")).to_have_text(t("stock.movements.title"))
    assert fits_screen_width(admin_page)
    booking = admin_page.locator("tr", has_text="Muster verschenkt")
    booking.get_by_role("button", name=t("stock.cancel"), exact=True).click()
    expect(admin_page.locator("tr", has_text="Muster verschenkt")).to_contain_text(
        t("stock.cancelled")
    )
    assert fits_screen_width(admin_page)

    admin_page.locator("header nav").get_by_text(t("nav.stock"), exact=True).click()
    expect(admin_page.locator("tr", has_text=number)).to_contain_text("10")


def test_refused_booking_shows_the_message_on_every_screen(admin_page: Page) -> None:
    admin_page.goto("/stock/new")
    admin_page.select_option("#type", "correction")
    admin_page.fill("#quantity", "0")
    admin_page.locator(f"button[type=submit]:has-text('{t('stock.book')}')").click()

    expect(admin_page.locator("#quantity-error")).to_be_visible()
    assert fits_screen_width(admin_page)
