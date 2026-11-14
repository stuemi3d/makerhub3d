"""Stock pop-ups and tables in a real browser, on desktop and phone (issue #87, test case 15)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width

BOOK = f"button[type=submit]:has-text('{t('stock.book')}')"


def add_article(page: Page, number: str, name: str) -> None:
    page.goto("/articles/new")
    page.fill("#number", number)
    page.fill("#name", name)
    page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(page.locator("h1")).to_contain_text(name)


def row_of(page: Page, text: str):
    return page.locator("table.data-table tbody tr").filter(has_text=text)


def test_booking_in_a_popup_updates_the_list_without_reload(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-S-{viewport_name}-1"
    add_article(admin_page, number, "Stapelhalter")
    admin_page.goto("/stock")
    admin_page.evaluate("window.__stays = true")
    assert fits_screen_width(admin_page)

    # The symbol in the row opens the pop-up with this article selected.
    row_of(admin_page, number).get_by_role("link", name=t("table.action.book")).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    expect(dialog.locator("#article_id option:checked")).to_contain_text(number)
    assert fits_screen_width(admin_page)
    dialog.locator("#type").select_option("correction")
    dialog.locator("#quantity").fill("10")
    dialog.locator("#reason").fill("Inventur")
    dialog.locator(BOOK).click()

    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, number)).to_contain_text("10")
    assert admin_page.url.endswith("/stock")
    assert admin_page.evaluate("window.__stays === true"), "the page was reloaded"
    assert fits_screen_width(admin_page)


def test_errors_stay_in_the_popup_and_only_cancel_closes_it(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-S-{viewport_name}-2"
    add_article(admin_page, number, "Fehlerprobe")
    admin_page.goto("/stock")

    admin_page.get_by_role("button", name=t("stock.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    dialog.locator("#article_id").select_option(label=f"{number} · Fehlerprobe")
    dialog.locator("#quantity").fill("5")
    dialog.locator("#reason").fill("Zu viel")
    dialog.locator(BOOK).click()

    expect(dialog.locator("#quantity-error")).to_be_visible()
    expect(dialog.locator("#reason")).to_have_value("Zu viel")
    admin_page.keyboard.press("Escape")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, number)).to_contain_text("0")


def test_movements_are_sortable_and_a_booking_can_be_cancelled(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-S-{viewport_name}-3"
    add_article(admin_page, number, "Bewegung")
    admin_page.goto(f"/articles/{admin_page.url.rsplit('/', 1)[-1]}/stock")
    admin_page.get_by_role("button", name=t("stock.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    dialog.locator("#type").select_option("correction")
    dialog.locator("#quantity").fill("4")
    dialog.locator("#reason").fill("Erste")
    dialog.locator(BOOK).click()
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, "Erste")).to_contain_text("+4")

    admin_page.goto("/stock/movements")
    assert fits_screen_width(admin_page)
    admin_page.locator("th a", has_text=t("stock.col.quantity")).click()
    expect(admin_page.locator("th[aria-sort='ascending']")).to_have_count(1)
    assert fits_screen_width(admin_page)

    row_of(admin_page, "Erste").get_by_role("button", name=t("table.action.cancel")).click()
    expect(row_of(admin_page, "Erste").first).to_contain_text(t("stock.cancelled"))
    assert fits_screen_width(admin_page)
