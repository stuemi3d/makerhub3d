"""Account, two-factor, login and setup pages in a real browser (issue #92, cases 7 and 8)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import ADMIN_PASSWORD, t
from tests.browser.test_theme import choose, open_appearance, theme_of


def fits_screen_width(page: Page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def start_two_factor_setup(page: Page) -> None:
    page.goto("/account")
    page.fill("#tfa_password", ADMIN_PASSWORD)
    page.click(f"button[type=submit]:has-text('{t('two_factor.setup.start')}')")
    expect(page.locator("h1")).to_have_text(t("two_factor.setup.title"))


def test_login_card_is_centered_and_fits(login_page: Page) -> None:
    card = login_page.locator("article.auth-box.detail-card")
    expect(card).to_be_visible()
    box = card.bounding_box()
    width = login_page.viewport_size["width"]
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert abs((box["x"] + box["width"] / 2) - width / 2) < 2
    assert fits_screen_width(login_page)


def test_account_cards_fit_in_both_themes(admin_page: Page) -> None:
    for theme in ("dark", "light"):
        open_appearance(admin_page)
        choose(admin_page, theme)
        admin_page.goto("/account")
        assert theme_of(admin_page) == theme
        expect(admin_page.locator("article.detail-card")).to_have_count(2)
        assert fits_screen_width(admin_page)


def test_two_factor_setup_page_is_a_card_that_fits(admin_page: Page) -> None:
    start_two_factor_setup(admin_page)

    expect(admin_page.locator("article.detail-card .qr-code img")).to_be_visible()
    assert fits_screen_width(admin_page)
