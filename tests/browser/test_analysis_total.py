"""Analysis of everything in a real browser, desktop and phone (issue #206, case 11)."""

from datetime import date
from decimal import Decimal

from playwright.sync_api import Page, expect
from sqlalchemy.orm import Session

from app import production, sales
from app.config import DATABASE_FILE
from app.db import make_engine
from app.models import Article
from app.production import PrintInput, ResultInput
from tests.browser.conftest import LiveServer, open_menu, t
from tests.browser.test_pages import fits_screen_width


def test_analysis_opens_from_the_menu_shows_figures_and_fits_the_screen(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    archive_id = 1000 if viewport_name == "desktop" else 1001
    engine = make_engine(live_server.data_dir / DATABASE_FILE)
    with Session(engine) as db, db.begin():
        article = Article(
            number=f"3D-GE-{viewport_name}",
            number_key=f"3d-ge-{viewport_name}",
            name="Gesamtprobe",
            active=True,
            created_at=date(2026, 1, 1),
        )
        db.add(article)
        db.flush()
        _, errors = production.book(
            db,
            None,
            date(2026, 3, 1),
            "",
            [
                PrintInput(
                    archive_id, Decimal("50.00"), Decimal(0), Decimal(0), waste_share=Decimal("0.5")
                )
            ],
            ResultInput(article.id, 10),
        )
        assert not errors
        values = {
            "article_id": article.id,
            "quantity": 4,
            "price": Decimal("9.00"),
            "date": date(2026, 3, 2),
            "note": "",
        }
        assert not sales.book(db, None, values)

    admin_page.goto("/")
    open_menu(admin_page)
    admin_page.locator("header nav").get_by_text(t("nav.analysis"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("analysis_total.title"))
    assert fits_screen_width(admin_page)

    admin_page.select_option("#period", "custom")
    admin_page.fill("#from", "2026-01-01")
    admin_page.fill("#to", "2026-12-31")
    admin_page.get_by_role("button", name=t("analysis_total.show")).click()
    expect(admin_page.locator(".stat")).to_have_count(5)
    expect(admin_page.locator("#top")).to_contain_text(f"3D-GE-{viewport_name}")
    expect(admin_page.locator("#top")).to_contain_text("16,00")
    assert fits_screen_width(admin_page)

    # The failed cost (here: waste of 25 EUR) with its share of the production cost and the
    # note on what is counted (issue 216, case 8)
    failed = admin_page.locator(".stat").filter(has_text=t("analysis_total.failed_cost"))
    expect(failed).to_contain_text(t("analysis_total.failed_share"))
    expect(failed).to_contain_text("%")
    expect(failed).to_contain_text(t("analysis_total.failed_note"))
    assert fits_screen_width(admin_page)

    # The table scrolls inside its card, never the page
    assert admin_page.locator("#top .data-scroll").evaluate(
        "el => el.scrollWidth >= el.clientWidth"
    )
