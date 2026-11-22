"""Filter bar on the article list in a browser, desktop and phone (issue #257, cases 1, 3, 4, 8)."""

import re

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_article_search import add_article
from tests.browser.test_pages import fits_screen_width


def open_filters(page: Page, viewport_name: str) -> None:
    """On the phone the fields are behind the "Filter" button; on the desktop they are open."""
    summary = page.locator("details[data-filter-bar] > summary")
    if viewport_name == "phone":
        expect(summary).to_be_visible()
        expect(page.locator("input[name=q]")).not_to_be_visible()
        summary.click()
    else:
        expect(summary).not_to_be_visible()
    expect(page.locator("input[name=q]")).to_be_visible()


def make_two(page: Page, viewport_name: str, tag: str = "a") -> tuple[str, str]:
    first, second = f"FB-{tag}-{viewport_name}-A1", f"FB-{tag}-{viewport_name}-B2"
    add_article(page, first, f"Zebrahaken {tag} {viewport_name}")
    add_article(page, second, f"Giraffenkasten {tag} {viewport_name}")
    return first, second


def test_search_chip_and_reset_work_in_the_browser(admin_page: Page, viewport_name: str) -> None:
    first, second = make_two(admin_page, viewport_name)
    admin_page.goto("/articles")
    assert fits_screen_width(admin_page)
    open_filters(admin_page, viewport_name)
    assert fits_screen_width(admin_page)

    rows = admin_page.locator("table.data-table tbody tr")
    # Typing filters after a short pause, without reloading the page
    admin_page.locator("input[name=q]").fill("zebrahaken")
    expect(rows.filter(has_text=first)).to_be_visible()
    expect(rows.filter(has_text=second)).to_have_count(0)
    # The filter is in the address
    expect(admin_page).to_have_url(re.compile(r"q=zebrahaken"))
    # ... and shown as a chip; the "x" removes just this filter
    chip = admin_page.locator("li.filter-chip")
    expect(chip).to_contain_text("zebrahaken")
    chip.locator("a").click()
    expect(rows.filter(has_text=second)).to_be_visible()
    expect(admin_page.locator("li.filter-chip")).to_have_count(0)

    # Nothing found: a hint, and "Reset" brings everything back
    admin_page.goto("/articles?q=gibtesnicht")
    expect(admin_page.get_by_text(t("filter.no_results"))).to_be_visible()
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("link", name=t("filter.reset")).first.click()
    expect(admin_page.locator("table.data-table")).to_be_visible()
    expect(admin_page.locator("li.filter-chip")).to_have_count(0)


def test_the_filter_count_follows_the_input_and_active_filters_stay_open(
    admin_page: Page, viewport_name: str
) -> None:
    make_two(admin_page, viewport_name, "b")
    admin_page.goto("/articles")
    open_filters(admin_page, viewport_name)
    admin_page.locator("input[name=q]").fill("zebrahaken")
    expect(admin_page.locator("li.filter-chip")).to_have_count(1)
    summary = admin_page.locator("details[data-filter-bar] > summary")
    # The count in the button is updated without a reload (visible on the phone)
    expect(summary).to_contain_text("(1)")
    # After a reload the fields stay open, because a filter is active
    admin_page.reload()
    expect(admin_page.locator("input[name=q]")).to_be_visible()
    assert fits_screen_width(admin_page)
