"""Chart on the analysis page in a real browser, dark and light, desktop and phone (issue #204)."""

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

# The chart canvas has drawn something: at least a few pixels differ from the card background
DRAWN_JS = """
() => {
  const canvas = document.querySelector("canvas[data-chart]");
  const context = canvas.getContext("2d");
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  let painted = 0;
  for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 0) painted += 1;
  return painted;
}
"""


def test_chart_is_drawn_in_both_themes_and_fits_the_screen(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    number = f"3D-CH-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Diagrammprobe")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Diagrammprobe")
    article_url = admin_page.url
    article_id = int(article_url.rstrip("/").rsplit("/", 1)[1])

    # Without a production: the hint instead of the chart
    admin_page.goto(f"{article_url}/analysis")
    expect(admin_page.locator("body")).to_contain_text(t("analysis.chart.empty"))
    expect(admin_page.locator("canvas")).to_have_count(0)

    archive_base = 800 if viewport_name == "desktop" else 810
    engine = make_engine(live_server.data_dir / DATABASE_FILE)
    with Session(engine) as db, db.begin():
        for offset, total in enumerate(("50.00", "70.00", "40.00")):
            _, errors = production.book(
                db,
                None,
                date(2026, 11, 10 + offset),
                "",
                [PrintInput(archive_base + offset, Decimal(total), Decimal(0), Decimal(0))],
                ResultInput(article_id, 10),
            )
            assert not errors
    engine.dispose()

    admin_page.goto(f"{article_url}/analysis")
    canvas = admin_page.locator("canvas[data-chart]")
    expect(canvas).to_be_visible()
    expect(admin_page.locator(".data-table tbody tr")).to_have_count(3)
    for theme in ("dark", "light"):
        admin_page.evaluate(
            "theme => document.documentElement.setAttribute('data-theme', theme)", theme
        )
        admin_page.wait_for_function(f"({DRAWN_JS})() > 200")
        assert fits_screen_width(admin_page)
        box = canvas.bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= admin_page.viewport_size["width"]


def test_hovering_the_chart_shows_the_values_at_the_mouse(
    admin_page: Page, live_server: LiveServer, viewport_name: str
) -> None:
    if viewport_name != "desktop":
        return  # there is no mouse on the phone
    admin_page.goto("/articles/new")
    admin_page.fill("#number", "3D-HOVER")
    admin_page.fill("#name", "Hoverprobe")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Hoverprobe")
    article_url = admin_page.url
    article_id = int(article_url.rstrip("/").rsplit("/", 1)[1])
    engine = make_engine(live_server.data_dir / DATABASE_FILE)
    with Session(engine) as db, db.begin():
        for offset in range(3):
            _, errors = production.book(
                db,
                None,
                date(2026, 11, 10 + offset),
                "",
                [PrintInput(900 + offset, Decimal("50.00"), Decimal(0), Decimal(0))],
                ResultInput(article_id, 10),
            )
            assert not errors
    engine.dispose()

    admin_page.goto(f"{article_url}/analysis")
    canvas = admin_page.locator("canvas[data-chart]")
    admin_page.wait_for_function(f"({DRAWN_JS})() > 200")
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    assert box is not None
    # Near the middle of the chart, not exactly on a point
    admin_page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 3)
    admin_page.mouse.move(box["x"] + box["width"] / 2 + 5, box["y"] + box["height"] / 3 + 2)
    admin_page.wait_for_function(
        """() => {
          const chart = Chart.getChart(document.querySelector("canvas[data-chart]"));
          return chart.tooltip.opacity > 0 && chart.tooltip.dataPoints.length > 0;
        }"""
    )
    shown = admin_page.evaluate(
        """() => {
          const chart = Chart.getChart(document.querySelector("canvas[data-chart]"));
          return chart.tooltip.dataPoints[0].parsed.y;
        }"""
    )
    assert shown == 5
