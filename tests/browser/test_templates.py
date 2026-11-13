"""Attributes and templates in a real browser, on desktop and phone (issue #44, test case 25)."""

import re

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def test_attribute_and_template_pages_work_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    attribute = f"Farbe-{viewport_name}"
    template = f"Halter-{viewport_name} mit einem ziemlich langen Namen zum Testen der Breite"
    admin_page.locator("header nav").get_by_text(t("nav.articles"), exact=True).click()
    admin_page.get_by_role("link", name=t("attributes.title"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("attributes.title"))
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("button", name=t("attributes.new"), exact=True).click()
    assert fits_screen_width(admin_page)
    admin_page.fill("#name", attribute)
    admin_page.fill("#values", "rot\nblau")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.get_by_text(attribute)).to_be_visible()
    expect(admin_page.get_by_text("rot, blau").first).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.goto(admin_page.url.rsplit("/", 1)[0] + "/templates")
    expect(admin_page.locator("h1")).to_have_text(t("templates.title"))
    admin_page.get_by_role("button", name=t("templates.new"), exact=True).click()
    assert fits_screen_width(admin_page)
    admin_page.fill("#name", template)
    admin_page.fill("#selling_price", "12,90")
    admin_page.get_by_label(attribute).check()
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()

    expect(admin_page.locator("h1")).to_contain_text("Halter")
    expect(admin_page.get_by_text(t("template.note"))).to_be_visible()
    expect(admin_page.get_by_text(re.compile(attribute + ": rot, blau"))).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("link", name=t("template.delete"), exact=True).click()
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("button", name=t("template.delete.button")).click()
    expect(admin_page.get_by_text(t("templates.empty"))).to_be_visible()
    assert fits_screen_width(admin_page)
