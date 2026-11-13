"""Dashboard in a real browser, on desktop and phone (issue #12, test case 13)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def test_dashboard_shows_tiles_and_low_stock_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-D-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Kabelhalter")
    admin_page.fill("#min_stock", "5")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Kabelhalter")

    admin_page.goto("/")

    expect(admin_page.locator("h1")).to_have_text(t("nav.dashboard"))
    expect(admin_page.locator(".stat")).to_have_count(3)
    expect(admin_page.locator(".stat").first).to_be_visible()
    low = admin_page.locator("#low-stock")
    expect(low).to_be_visible()
    expect(low.locator("tr", has_text=number)).to_be_visible()
    assert fits_screen_width(admin_page)
