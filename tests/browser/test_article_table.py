"""The article table in a real browser, on desktop and phone (issue #75, test case 13)."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def create_article(page: Page, number: str, name: str, price: str, min_stock: str) -> None:
    page.goto("/articles/new")
    page.fill("#number", number)
    page.fill("#name", name)
    page.fill("#selling_price", price)
    page.fill("#min_stock", min_stock)
    page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(page.locator("h1")).to_contain_text(name)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_table_sorts_and_fits_every_screen(
    admin_page: Page, viewport_name: str, theme: str
) -> None:
    low, high = f"3D-T-{viewport_name}-{theme}-A", f"3D-T-{viewport_name}-{theme}-B"
    create_article(admin_page, low, "Zange mit einem ziemlich langen Namen zum Testen", "5", "10")
    create_article(admin_page, high, "Axt", "20", "")

    admin_page.goto("/articles")
    admin_page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
    assert fits_screen_width(admin_page)
    # Stricter: nothing inside the table may widen the page (it scrolls inside its card).
    assert admin_page.evaluate("document.documentElement.scrollWidth <= document.body.clientWidth")
    rows = admin_page.locator("table.data-table tbody tr")
    row = rows.filter(has_text=low)
    expect(row.locator("a.icon-btn")).to_have_count(2)
    expect(row.locator("a.icon-btn").first).to_be_visible()
    expect(row.locator("meter.stock-meter")).to_be_visible()
    expect(rows.filter(has_text=high).locator("meter")).to_have_count(0)

    admin_page.locator("thead th a", has_text=t("article.field.name")).click()
    expect(admin_page.locator("thead th[aria-sort=ascending]")).to_contain_text(
        t("article.field.name")
    )
    assert fits_screen_width(admin_page)
    texts = rows.all_inner_texts()
    assert texts.index(next(x for x in texts if high in x)) < texts.index(
        next(x for x in texts if low in x)
    )

    row = admin_page.locator("table.data-table tbody tr").filter(has_text=low)
    row.get_by_role("link", name=t("table.action.edit")).click()
    expect(admin_page).to_have_url(re.compile(r"/articles/\d+/edit$"))
