"""Article detail, productions list in a real browser, desktop and phone (issue #120, case 10)."""

from datetime import date
from decimal import Decimal

from playwright.sync_api import Page, expect
from sqlalchemy.orm import Session

from app import production
from app.config import DATABASE_FILE
from app.db import make_engine
from app.production import PrintInput, ResultInput
from tests.browser.conftest import LiveServer, t
from tests.browser.test_pages import fits_screen_width


def test_productions_list_opens_from_the_tile_and_fits_the_screen(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    number = f"3D-AP-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Laterne")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Laterne")
    article_url = admin_page.url

    admin_page.get_by_role("link", name=t("article.tile.productions")).click()
    expect(admin_page.locator("body")).to_contain_text(t("article.productions.empty"))
    assert fits_screen_width(admin_page)

    # One live server for both views: every view books its own print
    archive_id = 500 if viewport_name == "desktop" else 501
    article_id = int(article_url.rstrip("/").rsplit("/", 1)[1])
    engine = make_engine(live_server.data_dir / DATABASE_FILE)
    with Session(engine) as db, db.begin():
        _, errors = production.book(
            db,
            None,
            date(2026, 11, 15),
            "",
            [PrintInput(archive_id, Decimal("12.00"), Decimal(0), Decimal(0))],
            ResultInput(article_id, 10),
        )
        assert not errors
    engine.dispose()

    admin_page.goto(article_url)
    admin_page.get_by_role("link", name=t("article.tile.productions")).click()
    row = admin_page.locator("tbody tr")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("1,20")
    expect(admin_page.locator("tfoot")).to_contain_text("10")
    assert fits_screen_width(admin_page)
