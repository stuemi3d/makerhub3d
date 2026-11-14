"""Attributes and templates in a real browser, on desktop and phone (issue #44, test case 25)."""

import re

from playwright.sync_api import Page, expect

from tests.browser.conftest import open_menu, t
from tests.browser.test_pages import fits_screen_width


def test_attribute_and_template_pages_work_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    attribute = f"Farbe-{viewport_name}"
    template = f"Halter-{viewport_name} mit einem ziemlich langen Namen zum Testen der Breite"
    open_menu(admin_page)
    admin_page.locator("header nav").get_by_text(t("nav.articles"), exact=True).click()
    admin_page.get_by_role("link", name=t("attributes.title"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("attributes.title"))
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("button", name=t("attributes.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    # An empty name keeps the pop-up open with the error; Cancel closes it without saving.
    dialog.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(dialog.locator("[aria-invalid=true]").first).to_be_visible()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()
    admin_page.get_by_role("button", name=t("attributes.new"), exact=True).click()
    admin_page.fill("#name", attribute)
    admin_page.fill("#values", "rot = rt\nblau = bl")
    dialog.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(dialog).not_to_be_visible()
    expect(admin_page.get_by_text(attribute)).to_be_visible()
    expect(admin_page.get_by_text("rot (rt), blau (bl)").first).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.goto(admin_page.url.rsplit("/", 1)[0] + "/templates")
    expect(admin_page.locator("h1")).to_have_text(t("templates.title"))
    admin_page.get_by_role("button", name=t("templates.new"), exact=True).click()
    template_dialog = admin_page.locator("dialog#popup")
    expect(template_dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    template_dialog.locator("#number").fill(f"B-T-{viewport_name}")
    template_dialog.locator("#name").fill(template)
    template_dialog.locator("#selling_price").fill("12,90")
    template_dialog.get_by_label(attribute).check()
    template_dialog.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()

    expect(template_dialog).not_to_be_visible()
    admin_page.get_by_role("link", name=re.compile("Halter")).first.click()
    expect(admin_page.locator("h1")).to_contain_text("Halter")
    expect(admin_page.get_by_text(t("template.note"))).to_be_visible()
    expect(
        admin_page.get_by_text(re.compile(re.escape(attribute) + ": rot .rt., blau .bl."))
    ).to_be_visible()
    assert fits_screen_width(admin_page)

    # The list with a row (long name, chip, icons) also fits the screen.
    admin_page.goto("/templates")
    expect(admin_page.locator("table.data-table .chip-active")).to_be_visible()
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("link", name=re.compile("Halter")).first.click()
    admin_page.get_by_role("link", name=t("template.delete"), exact=True).click()
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("button", name=t("template.delete.button")).click()
    expect(admin_page.get_by_text(t("templates.empty"))).to_be_visible()
    assert fits_screen_width(admin_page)
