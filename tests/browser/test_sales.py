"""Sales in a real browser, on desktop and phone (issue #201, test case 13)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width

SELL = f"button[type=submit]:has-text('{t('sales.book')}')"


def article_with_stock(page: Page, number: str, name: str, price: str) -> None:
    page.goto("/articles/new")
    page.fill("#number", number)
    page.fill("#name", name)
    page.fill("#selling_price", price)
    page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(page.locator("h1")).to_contain_text(name)
    page.goto("/stock/new")
    page.locator("#article_id").select_option(label=f"{number} · {name}")
    page.locator("#type").select_option("correction")
    page.fill("#quantity", "10")
    page.fill("#reason", "Start")
    page.locator(f"button[type=submit]:has-text('{t('stock.book')}')").click()


def row_of(page: Page, text: str):
    return page.locator("table.data-table tbody tr").filter(has_text=text)


def test_selling_and_cancelling_in_popups_without_reload(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-V-{viewport_name}-1"
    article_with_stock(admin_page, number, "Verkaufsprobe", "9,90")
    admin_page.goto("/sales")
    admin_page.evaluate("window.__stays = true")
    assert fits_screen_width(admin_page)

    admin_page.get_by_role("button", name=t("sales.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    # Choosing the article offers its target price
    dialog.locator("#article_id").select_option(label=f"{number} · Verkaufsprobe")
    expect(dialog.locator("#price")).to_have_value("9,90")
    dialog.locator("#quantity").fill("20")
    dialog.locator(SELL).click()
    expect(dialog.locator("#quantity-error")).to_be_visible()
    dialog.locator("#quantity").fill("4")
    dialog.locator(SELL).click()

    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, number)).to_contain_text("39,60")
    assert admin_page.evaluate("window.__stays === true"), "the page was reloaded"
    assert fits_screen_width(admin_page)

    row_of(admin_page, number).get_by_role("link", name=t("table.action.cancel")).click()
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name=t("sales.cancel.submit")).click()
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, number)).to_contain_text(t("sales.state.cancelled"))
    assert admin_page.evaluate("window.__stays === true"), "the page was reloaded"
