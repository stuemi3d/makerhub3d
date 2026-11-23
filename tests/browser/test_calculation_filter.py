"""Calculation list in a browser, desktop and phone (issue #260, cases 2, 3, 13, 16).

The calculations are written straight into the test database (the form is tested elsewhere);
every viewport has its own article numbers, because all browser tests share one database.
"""

import re
import sqlite3

from playwright.sync_api import Page, expect

from app.config import DATABASE_FILE
from tests.browser.conftest import LiveServer, t
from tests.browser.test_article_filter import open_filters
from tests.browser.test_article_search import add_article
from tests.browser.test_pages import fits_screen_width


def add_calculation(live_server: LiveServer, number: str, day: str, printer: str) -> None:
    with sqlite3.connect(live_server.data_dir / DATABASE_FILE) as db:
        (article_id,) = db.execute("SELECT id FROM articles WHERE number = ?", (number,)).fetchone()
        db.execute(
            "INSERT INTO calculations (article_id, created_on, created_at, kind, is_main,"
            " printer_name, print_minutes, pieces, setup_minutes, rework_minutes, scrap_percent,"
            " margin_percent, power_price, machine_rate, labour_rate, rounding, material_per_plate,"
            " power_per_plate, machine_per_plate, labour_cost, cost_without_labour,"
            " cost_with_labour, suggested_price, selling_price, profit)"
            " VALUES (?, ?, ?, 'hand', 0, ?, '60', 4, '0', '0', '10', '30', '0.3', '1', '20',"
            " 'none', '12', '8', '4', '0.5', '6.6', '7.1', '9.23', '9.23', '2.13')",
            (article_id, day, f"{day} 12:00:00", printer),
        )


def test_the_list_has_the_new_columns_and_filters_in_the_browser(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    first, second = f"KF-{viewport_name}-A1", f"KF-{viewport_name}-B2"
    add_article(admin_page, first, "Zebrahaken")
    add_article(admin_page, second, "Giraffenkasten")
    add_calculation(live_server, first, "2026-03-01", f"Blau {viewport_name}")
    add_calculation(live_server, second, "2026-03-20", f"Rot {viewport_name}")

    admin_page.goto("/calculation")
    assert fits_screen_width(admin_page)
    row = admin_page.locator("tr.calc-row").filter(has_text=first)
    # Number and name in their own columns; the date is no link, the eye opens the pop-up
    expect(row.locator("td.col-number")).to_have_text(first)
    expect(row.locator("td.col-name")).to_have_text("Zebrahaken")
    expect(row.locator("td.col-date a")).to_have_count(0)
    expect(row.locator("td.col-material")).to_have_text("3,30")
    expect(row.locator("td.col-profit")).to_have_text("2,13")
    row.locator("a.icon-btn", has_text=t("table.action.view")).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog.get_by_text(t("calc.result.suggested"))).to_be_visible()
    dialog.get_by_role("button", name=t("calc.view.close")).click()
    expect(dialog).not_to_be_visible()

    # The symbol on the right leads to the article
    row.locator("a.icon-btn", has_text=t("table.action.article")).click()
    expect(admin_page.locator("h1")).to_contain_text("Zebrahaken")

    # Filters: search, period and printer; chips; reset
    admin_page.goto("/calculation")
    open_filters(admin_page, viewport_name)
    assert fits_screen_width(admin_page)
    rows = admin_page.locator("tr.calc-row")
    admin_page.locator("input[name=q]").fill("giraffen")
    expect(rows.filter(has_text=second)).to_be_visible()
    expect(rows.filter(has_text=first)).to_have_count(0)
    expect(admin_page).to_have_url(re.compile(r"q=giraffen"))
    admin_page.locator("li.filter-chip a").click()
    expect(rows.filter(has_text=first)).to_be_visible()

    # After the reload the bar is closed again on the phone (no filter is active)
    open_filters(admin_page, viewport_name)
    admin_page.locator("input[name=from]").fill("2026-03-10")
    expect(rows.filter(has_text=first)).to_have_count(0)
    expect(rows.filter(has_text=second)).to_be_visible()
    admin_page.locator("select[name=printer]").select_option(f"Rot {viewport_name}")
    expect(admin_page.locator("li.filter-chip")).to_have_count(2)
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("link", name=t("filter.reset")).first.click()
    expect(rows.filter(has_text=first)).to_be_visible()
    expect(admin_page.locator("li.filter-chip")).to_have_count(0)
