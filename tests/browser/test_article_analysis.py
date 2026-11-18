"""Analysis page of an article in a real browser, desktop and phone (issue #203, case 14)."""

from datetime import date
from decimal import Decimal

from playwright.sync_api import Page, expect
from sqlalchemy.orm import Session

from app import production
from app.config import DATABASE_FILE
from app.db import make_engine
from app.models import Calculation
from app.production import PrintInput, ResultInput
from tests.browser.conftest import LiveServer, t
from tests.browser.test_pages import fits_screen_width


def test_analysis_opens_from_the_tile_fits_the_screen_and_warns(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    number = f"3D-AN-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Auswertungsprobe")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Auswertungsprobe")
    article_url = admin_page.url
    article_id = int(article_url.rstrip("/").rsplit("/", 1)[1])

    admin_page.get_by_role("link", name=t("article.tile.analysis")).click()
    expect(admin_page.locator("h1")).to_contain_text(t("article.tile.analysis"))
    expect(admin_page.locator(".stat")).to_have_count(11)
    expect(admin_page.locator("body")).not_to_contain_text(t("analysis.warning"))
    assert fits_screen_width(admin_page)

    # 10 pieces for 60 Euro against a calculation of 5 Euro: 20 % over the threshold of 15 %
    archive_id = 700 if viewport_name == "desktop" else 701
    engine = make_engine(live_server.data_dir / DATABASE_FILE)
    with Session(engine) as db, db.begin():
        _, errors = production.book(
            db,
            None,
            date(2026, 11, 15),
            "",
            [PrintInput(archive_id, Decimal("60.00"), Decimal(0), Decimal(0))],
            ResultInput(article_id, 10),
        )
        assert not errors
        db.add(
            Calculation(
                article_id=article_id,
                created_on=date(2026, 11, 1),
                created_at=date(2026, 11, 1),
                kind="hand",
                is_main=True,
                printer_name="Testdrucker",
                print_minutes=Decimal(60),
                pieces=1,
                setup_minutes=Decimal(0),
                rework_minutes=Decimal(0),
                scrap_percent=Decimal(0),
                margin_percent=Decimal(0),
                power_price=Decimal("0.3"),
                machine_rate=Decimal(0),
                labour_rate=Decimal(0),
                rounding="cent",
                material_per_plate=Decimal(0),
                power_per_plate=Decimal(0),
                machine_per_plate=Decimal(0),
                labour_cost=Decimal(0),
                cost_without_labour=Decimal(5),
                cost_with_labour=Decimal(5),
                suggested_price=Decimal(0),
                selling_price=Decimal(0),
                profit=Decimal(0),
            )
        )
    engine.dispose()

    admin_page.goto(f"{article_url}/analysis")
    expect(admin_page.locator("body")).to_contain_text("6,00")
    expect(admin_page.locator(".chip-warn")).to_contain_text(t("analysis.warning"))
    assert fits_screen_width(admin_page)
