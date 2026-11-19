"""Page width on the desktop (issue #225): key figures and period forms stay compact."""

import pytest
from playwright.sync_api import Page


def box(page: Page, selector: str) -> dict:
    return page.eval_on_selector(selector, "el => el.getBoundingClientRect().toJSON()")


@pytest.mark.parametrize("path", ["/analysis", "/"])
def test_period_form_and_stat_tiles_are_not_stretched(admin_page: Page, path: str) -> None:
    if admin_page.viewport_size["width"] < 900:
        pytest.skip("the phone is narrow anyway")
    admin_page.goto(path)
    form = box(admin_page, ".period-form")
    assert form["width"] <= 51 * 16
    button = box(admin_page, ".period-form button[type=submit]")
    assert button["width"] < 300
    main = box(admin_page, "main")
    for tile in admin_page.locator(".stat").all():
        assert tile.evaluate("el => el.getBoundingClientRect().width") <= 19 * 16
    # left aligned: the first tile starts at the left edge of the page content
    first = box(admin_page, ".stat")
    assert abs(first["left"] - main["left"]) < 40
