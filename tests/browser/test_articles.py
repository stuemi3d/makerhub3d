"""Articles in a real browser, on desktop and phone (issue #10, test case 14)."""

import re

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width

SECTIONS = ["calculations", "productions", "stock", "sales", "analysis"]


def test_article_list_form_and_detail_work_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-B-{viewport_name}"
    admin_page.locator("header nav").get_by_text(t("nav.articles"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("articles.title"))
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("button", name=t("articles.new")).click()
    assert fits_screen_width(admin_page)
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Kabelhalter mit einem ziemlich langen Namen zum Testen der Breite")
    admin_page.fill("#selling_price", "12,90")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()

    expect(admin_page.locator("h1")).to_contain_text("Kabelhalter")
    assert fits_screen_width(admin_page)
    for section in SECTIONS:
        expect(
            admin_page.get_by_role("link", name=re.compile(t(f"article.tile.{section}")))
        ).to_be_visible()

    admin_page.get_by_role("link", name=re.compile(t("article.tile.stock"))).click()
    expect(admin_page.get_by_text(t("article.section.empty"))).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("link", name=t("article.back")).first.click()
    admin_page.get_by_role("link", name=t("articles.title")).first.click()
    expect(admin_page.get_by_text(number)).to_be_visible()
    assert fits_screen_width(admin_page)
