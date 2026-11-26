"""Production list in a browser, desktop and phone (issue #259, case 10).

The productions are written straight into the test database (booking is tested elsewhere);
every viewport has its own article numbers, because all browser tests share one database.
"""

import re
import sqlite3

from playwright.sync_api import Page, expect

from app.config import DATABASE_FILE
from tests.browser.conftest import LiveServer, t
from tests.browser.test_article_search import add_article
from tests.browser.test_pages import fits_screen_width


def add_production(
    live_server: LiveServer, number: str, day: str, printer: str, cancelled: int = 0
) -> None:
    with sqlite3.connect(live_server.data_dir / DATABASE_FILE) as db:
        (article_id,) = db.execute("SELECT id FROM articles WHERE number = ?", (number,)).fetchone()
        production_id = db.execute(
            "INSERT INTO productions (booked_on, created_at, note, total_cost, cancelled)"
            " VALUES (?, ?, '', '4', ?)",
            (day, f"{day} 12:00:00", cancelled),
        ).lastrowid
        db.execute(
            "INSERT INTO production_prints (production_id, archive_id, material_cost,"
            " electricity_cost, wear_cost, machine_cost, status, printer_name)"
            " VALUES (?, 1, '4', '0', '0', '0', 'success', ?)",
            (production_id, printer),
        )
        db.execute(
            "INSERT INTO production_results (production_id, article_id, quantity,"
            " allocated_cost, movement_id) VALUES (?, ?, 2, '4', 0)",
            (production_id, article_id),
        )


def open_filters(page: Page, viewport_name: str) -> None:
    """On the phone the fields are behind the "Filter" button; on the desktop they are open."""
    summary = page.locator("details[data-filter-bar] > summary")
    field = page.locator("select[name=printer]")
    if viewport_name == "phone":
        expect(summary).to_be_visible()
        expect(field).not_to_be_visible()
        summary.click()
    else:
        expect(summary).not_to_be_visible()
    expect(field).to_be_visible()


def test_the_list_has_the_printer_column_and_filters_in_the_browser(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    first, second = f"PF-{viewport_name}-A1", f"PF-{viewport_name}-B2"
    add_article(admin_page, first, "Zebrahaken")
    add_article(admin_page, second, "Giraffenkasten")
    add_production(live_server, first, "2026-03-01", f"Blau {viewport_name}")
    add_production(live_server, second, "2026-03-20", f"Rot {viewport_name}", cancelled=1)

    admin_page.goto("/production/list")
    assert fits_screen_width(admin_page)
    rows = admin_page.locator("tr.production-row")
    expect(rows.filter(has_text=f"Blau {viewport_name}").locator("td.col-printer")).to_have_text(
        f"Blau {viewport_name}"
    )

    open_filters(admin_page, viewport_name)
    assert fits_screen_width(admin_page)
    admin_page.locator("select[name=printer]").select_option(f"Rot {viewport_name}")
    expect(rows.filter(has_text=f"Rot {viewport_name}")).to_be_visible()
    expect(rows.filter(has_text=f"Blau {viewport_name}")).to_have_count(0)
    expect(admin_page).to_have_url(re.compile(r"printer="))
    admin_page.locator("select[name=status]").select_option("booked")
    expect(admin_page.get_by_text(t("filter.no_results"))).to_be_visible()
    expect(admin_page.locator("li.filter-chip")).to_have_count(2)
    assert fits_screen_width(admin_page)
    admin_page.get_by_role("link", name=t("filter.reset")).first.click()
    expect(rows.filter(has_text=f"Blau {viewport_name}")).to_be_visible()
    expect(admin_page.locator("li.filter-chip")).to_have_count(0)
