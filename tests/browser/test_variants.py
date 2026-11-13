"""Variants, inheriting and deleting in a real browser, on desktop and phone (issue #49)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def submit(page: Page, key: str) -> None:
    page.locator(f"button[type=submit]:has-text('{t(key)}')").click()


def test_variant_inherits_shows_it_and_can_be_deleted_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    attribute = f"Farbe-{viewport_name}"
    template = f"Halter-{viewport_name}"
    number = f"3D-V-{viewport_name}"

    admin_page.goto("/attributes/new")
    admin_page.fill("#name", attribute)
    admin_page.fill("#values", "rot\nblau")
    submit(admin_page, "article.create")
    admin_page.goto("/templates/new")
    admin_page.fill("#name", template)
    admin_page.fill("#selling_price", "12,90")
    admin_page.get_by_label(attribute).check()
    submit(admin_page, "article.create")
    expect(admin_page.locator("h1")).to_have_text(template)

    admin_page.get_by_role("button", name=t("variant.new"), exact=True).click()
    assert fits_screen_width(admin_page)
    admin_page.fill("#number", number)
    admin_page.fill("#name", f"{template} rot")
    admin_page.locator("select[name^=attr_]").select_option(label="rot")
    submit(admin_page, "article.create")

    expect(admin_page.locator("h1")).to_contain_text(number)
    expect(admin_page.get_by_text(t("variant.inherited")).first).to_be_visible()
    expect(admin_page.get_by_role("link", name=template, exact=True)).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("button", name=t("article.edit"), exact=True).click()
    admin_page.fill("#selling_price", "15,00")
    submit(admin_page, "article.save")
    expect(admin_page.get_by_text(t("variant.own"))).to_be_visible()
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("button", name=t("variant.reset"), exact=True).click()
    expect(admin_page.get_by_text(t("variant.own"))).to_have_count(0)
    expect(admin_page.get_by_text("12,90")).to_be_visible()

    admin_page.get_by_role("link", name=template, exact=True).click()
    expect(admin_page.get_by_role("link", name=number, exact=True)).to_be_visible()
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("link", name=number, exact=True).click()

    admin_page.get_by_role("button", name=t("article.delete"), exact=True).click()
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("button", name=t("article.delete.button")).click()
    expect(admin_page.get_by_role("link", name=number, exact=True)).to_have_count(0)
