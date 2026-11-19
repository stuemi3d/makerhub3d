"""Dashboard in a real browser, on desktop and phone (issue #12, test case 13)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def test_dashboard_shows_tiles_and_low_stock_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-D-{viewport_name}"
    admin_page.goto("/articles/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Kabelhalter")
    admin_page.fill("#min_stock", "5")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator("h1")).to_contain_text("Kabelhalter")

    admin_page.goto("/")

    expect(admin_page.locator("h1")).to_have_text(t("nav.dashboard"))
    expect(admin_page.locator(".stat")).to_have_count(14)
    expect(admin_page.locator(".stat").first).to_be_visible()
    low = admin_page.locator("#low-stock")
    expect(low).to_be_visible()
    expect(low.locator("tr", has_text=number)).to_be_visible()
    assert fits_screen_width(admin_page)


def test_dashboard_areas_and_chart_fit_every_screen(admin_page: Page) -> None:
    """Issue #207, test case 8: all areas, the period choice and the drawn chart fit the width."""
    admin_page.goto("/")

    for key in ("group.sales", "group.production", "group.quality", "group.stock", "todo.title"):
        expect(admin_page.locator("h2", has_text=t(f"dashboard.{key}"))).to_be_visible()
    expect(admin_page.locator("#todo")).to_be_visible()
    canvas = admin_page.locator("#month-chart canvas[data-chart]")
    canvas.scroll_into_view_if_needed()
    expect(canvas).to_be_visible()
    admin_page.wait_for_function(
        """() => {
          const c = document.querySelector("canvas[data-chart]");
          const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
          let n = 0;
          for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n += 1;
          return n > 200;
        }"""
    )
    assert fits_screen_width(admin_page)
    box = canvas.bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= admin_page.viewport_size["width"]


def test_dates_are_greyed_out_until_the_free_period_is_chosen(admin_page: Page) -> None:
    """Dates only count for the free period, so they are disabled before (issue #207)."""
    admin_page.goto("/?period=last_month")
    expect(admin_page.locator("#from")).to_be_disabled()
    expect(admin_page.locator("#to")).to_be_disabled()

    admin_page.select_option("#period", "custom")
    expect(admin_page.locator("#from")).to_be_enabled()
    admin_page.fill("#from", "2026-01-05")
    admin_page.fill("#to", "2026-01-06")
    admin_page.locator(".period-form button[type=submit]").click()

    expect(admin_page.locator("#period")).to_have_value("custom")
    expect(admin_page.locator("#from")).to_have_value("2026-01-05")
    expect(admin_page.locator("#to")).to_have_value("2026-01-06")
    admin_page.select_option("#period", "month")
    expect(admin_page.locator("#from")).to_be_disabled()
    expect(admin_page.locator("#from")).to_have_value("")


def test_card_headings_stay_inside_their_cards(admin_page: Page) -> None:
    """The heading bar of a card must not stick out of it (a header element does, see Pico)."""
    for path, card in (("/analysis", "#top"), ("/", "#month-chart")):
        admin_page.goto(path)
        box = admin_page.locator(card).bounding_box()
        head = admin_page.locator(f"{card} .card-head").bounding_box()
        assert box is not None and head is not None
        assert head["x"] >= box["x"]
        assert head["x"] + head["width"] <= box["x"] + box["width"] + 0.5
