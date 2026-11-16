"""Deleting an article and an attribute as pop-ups, on desktop and phone (issue 169)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def test_an_article_is_deleted_in_a_popup_and_leads_to_the_list(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"LOESCH-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Wegwerf")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Wegwerf")
    article_page = admin_page.url

    # The question is a pop-up of the app; Cancel keeps the article.
    admin_page.get_by_role("button", name=t("article.delete"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog.locator("h1")).to_have_text(t("article.delete"))
    expect(dialog).to_contain_text(number)
    assert fits_screen_width(admin_page)
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()
    assert admin_page.url == article_page

    # Confirming leads to the list, because the page behind the pop-up is gone.
    admin_page.get_by_role("button", name=t("article.delete"), exact=True).click()
    dialog.get_by_role("button", name=t("article.delete.button")).click()
    admin_page.wait_for_url("**/articles")
    expect(admin_page.locator("h1")).to_have_text(t("articles.title"))
    expect(admin_page.locator("table.data-table td.cell-strong", has_text=number)).to_have_count(0)
    assert fits_screen_width(admin_page)


def test_an_attribute_is_deleted_in_a_popup_and_the_list_refreshes(
    admin_page: Page, viewport_name: str
) -> None:
    name = f"Gewicht-{viewport_name}"
    admin_page.goto("/attributes")
    admin_page.get_by_role("button", name=t("attributes.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    dialog.locator("#name").fill(name)
    dialog.locator("#values").fill("leicht = lt\nschwer = sw")
    dialog.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(dialog).not_to_be_visible()
    card = admin_page.locator("article").filter(has_text=name)
    expect(card).to_have_count(1)

    card.get_by_role("link", name=t("attribute.delete")).click()
    expect(dialog.locator("h1")).to_have_text(t("attribute.delete"))
    assert fits_screen_width(admin_page)
    dialog.get_by_role("button", name=t("attribute.delete.button")).click()
    expect(dialog).not_to_be_visible()
    expect(admin_page.locator("article").filter(has_text=name)).to_have_count(0)
    assert fits_screen_width(admin_page)
