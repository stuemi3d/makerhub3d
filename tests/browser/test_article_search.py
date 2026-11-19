"""The article choice is searchable by number and name on every page (issue #222)."""

import re

from playwright.sync_api import Page, expect

from tests.browser.conftest import pick_article, t
from tests.browser.test_pages import fits_screen_width


def add_article(page: Page, number: str, name: str) -> None:
    page.goto("/articles/new")
    page.fill("#number", number)
    page.fill("#name", name)
    page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(page.locator("h1")).to_contain_text(name)


def make_two(page: Page, tag: str) -> tuple[str, str]:
    first, second = f"SU-{tag}-A1", f"SU-{tag}-B2"
    add_article(page, first, "Wandhaken")
    add_article(page, second, "Blumenkasten")
    return first, second


def test_the_stock_form_searches_by_number_and_by_name(
    admin_page: Page, viewport_name: str
) -> None:
    first, second = make_two(admin_page, f"st-{viewport_name}")
    admin_page.goto("/stock")
    admin_page.get_by_role("button", name=t("stock.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    field = dialog.locator("#article_id-combo")
    expect(field).to_be_visible()
    assert fits_screen_width(admin_page)
    # The cursor is in the search field right away
    expect(field).to_be_focused()

    field.click()
    expect(dialog.locator("#article_id-list li[data-value]")).not_to_have_count(0)
    # By a part of the number (case does not matter)
    field.fill(f"su-st-{viewport_name}-b2".upper())
    expect(dialog.locator("#article_id-list li", has_text=second)).to_be_visible()
    expect(dialog.locator("#article_id-list li", has_text=first)).to_have_count(0)
    # By a part of the name
    field.fill("wandh")
    expect(dialog.locator("#article_id-list li", has_text=first)).to_be_visible()
    expect(dialog.locator("#article_id-list li", has_text=second)).to_have_count(0)
    # Nothing found
    field.fill("gibtesnicht")
    expect(dialog.locator("#article_id-list li.combo-none")).to_have_text(t("article.search_none"))
    # Choosing sets the real field that is sent
    pick_article(dialog, "article_id", f"{second} · Blumenkasten")
    expect(dialog.locator("#article_id option:checked")).to_contain_text(second)


def test_the_filters_reload_after_choosing_an_article(admin_page: Page, viewport_name: str) -> None:
    first, _second = make_two(admin_page, f"fl-{viewport_name}")
    for path, field in (("/sales", "article"), ("/stock/movements", "article")):
        admin_page.goto(path)
        assert fits_screen_width(admin_page)
        pick_article(admin_page, field, f"{first} · Wandhaken")
        expect(admin_page).to_have_url(re.compile(rf"{re.escape(path)}\?article=\d+"))
        expect(admin_page.locator("#article-combo")).to_have_value(f"{first} · Wandhaken")
        # "All articles" stays available
        admin_page.locator("#article-combo").click()
        expect(admin_page.locator("#article-list li[data-value='']")).to_have_text(
            t("stock.filter.all")
        )


def test_the_sale_form_has_the_search(admin_page: Page, viewport_name: str) -> None:
    _first, second = make_two(admin_page, f"pr-{viewport_name}")
    admin_page.goto("/sales")
    admin_page.get_by_role("button", name=t("sales.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog.locator("#article_id-combo")).to_be_visible()
    dialog.locator("#article_id-combo").fill(second)
    expect(dialog.locator("#article_id-list li:not([data-value=''])")).to_have_count(1)
