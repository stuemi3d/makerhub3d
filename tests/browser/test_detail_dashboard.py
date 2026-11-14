"""Article detail and dashboard in a real browser, desktop and phone (issue #77).

Test cases 4, 5, 9 and 12 of the comment on the issue.
"""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width

CREATE = f"button[type=submit]:has-text('{t('article.create')}')"
SAVE = f"button[type=submit]:has-text('{t('article.save')}')"


def create(page: Page, number: str, name: str, min_stock: str = "") -> None:
    page.goto("/articles/new")
    page.fill("#number", number)
    page.fill("#name", name)
    page.fill("#min_stock", min_stock)
    page.locator(CREATE).click()
    expect(page.locator("h1")).to_contain_text(name)


def test_edit_on_the_detail_updates_heading_and_path_without_reload(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-S-{viewport_name}"
    create(admin_page, number, "Vorher")
    admin_page.evaluate("window.__stays = true")

    admin_page.get_by_role("button", name=t("article.edit")).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog.locator("h1")).to_have_text(t("article.edit.title"))
    dialog.locator("#number").fill(number + "-X")
    dialog.locator("#name").fill("Nachher")
    dialog.locator(SAVE).click()

    expect(dialog).not_to_be_visible()
    expect(admin_page.locator("main h1")).to_have_text(f"{number}-X · Nachher")
    expect(admin_page.get_by_label("breadcrumb")).to_contain_text(f"{number}-X")
    expect(admin_page.locator("article.detail-card")).to_contain_text("Nachher")
    assert admin_page.evaluate("window.__stays === true"), "the page was reloaded"
    assert fits_screen_width(admin_page)
    # The tiles are still there and still lead on.
    expect(admin_page.locator("a.tile")).to_have_count(5)


def test_edit_errors_on_the_detail_stay_in_the_pop_up(admin_page: Page, viewport_name: str) -> None:
    create(admin_page, f"3D-S-{viewport_name}-E", "Fehler")

    admin_page.get_by_role("button", name=t("article.edit")).click()
    dialog = admin_page.locator("dialog#popup")
    dialog.locator("#name").fill("")
    dialog.locator(SAVE).click()

    expect(dialog).to_be_visible()
    expect(dialog.locator("[aria-invalid=true]").first).to_be_focused()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()
    expect(admin_page.locator("main h1")).to_contain_text("Fehler")


def test_dashboard_table_sorts_and_fits_every_screen(admin_page: Page, viewport_name: str) -> None:
    short, long = f"3D-S-{viewport_name}-K", f"3D-S-{viewport_name}-L"
    create(admin_page, short, "Zange mit einem ziemlich langen Namen zum Testen", "10")
    create(admin_page, long, "Axt", "10")

    admin_page.goto("/")
    low = admin_page.locator("#low-stock")
    rows = low.locator("table.data-table tbody tr")
    expect(rows.filter(has_text=short)).to_be_visible()
    expect(admin_page.locator(".stat")).to_have_count(3)
    assert fits_screen_width(admin_page)
    assert admin_page.evaluate("document.documentElement.scrollWidth <= document.body.clientWidth")

    low.get_by_role("link", name=t("article.field.number")).click()
    expect(admin_page).to_have_url(f"{admin_page.url.split('?')[0]}?sort=number&dir=asc")
    texts = rows.all_inner_texts()
    assert texts.index(next(x for x in texts if short in x)) < texts.index(
        next(x for x in texts if long in x)
    )
    rows.filter(has_text=short).get_by_role("link", name=t("table.action.view")).click()
    expect(admin_page).to_have_url(f"{admin_page.url.rsplit('/', 1)[0]}/stock")
