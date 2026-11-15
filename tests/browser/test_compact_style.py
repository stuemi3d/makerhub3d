"""Compact look (issue #109): small base text and flat tab bar, on desktop and phone."""

from playwright.sync_api import Page


def px(page: Page, selector: str, prop: str) -> float:
    value = page.eval_on_selector(selector, f"el => getComputedStyle(el).{prop}")
    return float(value.removesuffix("px"))


def test_text_is_compact_on_every_width(admin_page: Page) -> None:
    admin_page.goto("/articles")
    assert px(admin_page, "body", "fontSize") <= 15
    assert px(admin_page, "h1", "fontSize") <= 26


def test_area_tabs_are_flat_with_a_line_below(admin_page: Page) -> None:
    admin_page.goto("/articles")
    assert px(admin_page, '.area-tabs a[aria-current="page"]', "borderTopLeftRadius") == 0
    assert px(admin_page, ".area-tabs ul", "borderBottomWidth") == 1
    assert px(admin_page, '.area-tabs a[aria-current="page"]', "borderBottomWidth") == 2


def test_settings_tabs_look_like_the_area_tabs(admin_page: Page) -> None:
    admin_page.goto("/settings")
    assert px(admin_page, '.settings-tabs a[aria-current="page"]', "borderTopLeftRadius") == 0
    assert px(admin_page, ".settings-tabs ul", "borderBottomWidth") == 1
    assert px(admin_page, '.settings-tabs a[aria-current="page"]', "borderBottomWidth") == 2
