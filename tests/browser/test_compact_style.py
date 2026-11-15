"""Compact look (issue #109): small base text and flat tab bar, on desktop and phone."""

import pytest
from playwright.sync_api import Page

from tests.browser.conftest import t


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


def width(page: Page, selector: str) -> float:
    return page.eval_on_selector(selector, "el => el.getBoundingClientRect().width")


def test_forms_are_not_stretched_over_the_whole_width(admin_page: Page) -> None:
    """Settings, account, appearance and attributes: cards and buttons keep a readable width."""
    if admin_page.viewport_size["width"] < 900:
        pytest.skip("the phone is narrow anyway")
    admin_page.goto("/attributes/new")
    admin_page.fill("#name", "Farbe")
    admin_page.fill("#values", "rot = rt")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    admin_page.goto("/attributes")
    cards = (
        ("/settings", "main form > .detail-card"),
        ("/account", "main .detail-card"),
        ("/settings/appearance", "main form > .detail-card"),
        ("/attributes", "main .detail-card"),
    )
    for path, card in cards:
        admin_page.goto(path)
        assert width(admin_page, card) <= 576, path
    admin_page.goto("/settings")
    assert width(admin_page, "main form > button[type=submit]") <= 300
