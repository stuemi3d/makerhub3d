"""Deleting an article for good in a real browser, on desktop and phone (issue #47, case 14)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def test_admin_deletes_an_article_with_stock_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-P-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Kabelhalter")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Kabelhalter")
    # Nothing hangs on the article yet, so there is no button.
    expect(admin_page.get_by_text(t("article.purge.button"), exact=True)).to_have_count(0)

    admin_page.goto("/stock/new")
    admin_page.select_option("#article_id", label=f"{number} · Kabelhalter")
    admin_page.select_option("#type", "correction")
    admin_page.fill("#quantity", "5")
    admin_page.fill("#reason", "Inventur")
    admin_page.locator(f"button[type=submit]:has-text('{t('stock.book')}')").click()

    admin_page.goto("/articles")
    admin_page.get_by_role("link", name=number, exact=True).click()
    admin_page.get_by_role("button", name=t("article.purge.button"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("article.purge.title"))
    expect(admin_page.get_by_text(t("article.purge.irreversible"))).to_be_visible()
    assert fits_screen_width(admin_page)

    submit = admin_page.locator(f"button[type=submit]:has-text('{t('article.purge.confirm')}')")
    admin_page.fill("input[name=number]", "falsch")
    submit.click()
    expect(admin_page.get_by_text(t("article.purge.mismatch"))).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.fill("input[name=number]", number)
    submit.click()
    expect(admin_page.get_by_text(t("article.purge.done"))).to_be_visible()
    expect(admin_page.get_by_role("link", name=number, exact=True)).to_have_count(0)
    assert fits_screen_width(admin_page)
