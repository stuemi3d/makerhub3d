"""Dark and light theme in a real browser (issue #73), on desktop and on the phone."""

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import LiveServer, t

PAGES = ["/settings", "/settings/appearance", "/account"]
CONTRAST_JS = """
() => {
  const parse = (value) => value.match(/[\\d.]+/g).slice(0, 4).map(Number);
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const backdrop = (el) => {
    for (let node = el; node; node = node.parentElement) {
      const c = parse(getComputedStyle(node).backgroundColor);
      if (c.length < 4 || c[3] > 0.99) return c;
    }
    return [255, 255, 255, 1];
  };
  const result = {};
  for (const [name, selector] of Object.entries(SELECTORS)) {
    const el = document.querySelector(selector);
    if (!el) { result[name] = null; continue; }
    const fg = lum(parse(getComputedStyle(el).color));
    const bg = lum(backdrop(el));
    result[name] = (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
  }
  return result;
}
""".replace("SELECTORS", "%s")
SELECTORS = {
    "text": "main h1",
    "muted": "main small",
    "button": "main form button[type=submit]",
    "input": "main input[type=text]",
    "link": ".settings-tabs a:not([aria-current])",
}


def theme_of(page: Page) -> str:
    return page.evaluate("document.documentElement.getAttribute('data-theme')")


def open_appearance(page: Page) -> None:
    page.goto("/settings/appearance")


def choose(page: Page, theme: str) -> None:
    page.check(f"input[name=theme][value={theme}]")


def test_first_visit_is_dark(admin_page: Page) -> None:
    assert theme_of(admin_page) == "dark"


def test_switch_changes_the_look_at_once_without_reloading(admin_page: Page) -> None:
    open_appearance(admin_page)
    admin_page.evaluate("window.__same_page = true")
    dark = admin_page.evaluate("getComputedStyle(document.documentElement).backgroundColor")

    choose(admin_page, "light")

    assert theme_of(admin_page) == "light"
    assert admin_page.evaluate("window.__same_page") is True
    assert admin_page.evaluate("getComputedStyle(document.documentElement).backgroundColor") != dark
    choose(admin_page, "dark")
    assert theme_of(admin_page) == "dark"


def test_choice_is_kept_after_reload_and_on_other_pages(admin_page: Page) -> None:
    open_appearance(admin_page)
    choose(admin_page, "light")

    admin_page.reload()
    assert theme_of(admin_page) == "light"
    expect(admin_page.locator("input[name=theme][value=light]")).to_be_checked()
    admin_page.goto("/account")
    assert theme_of(admin_page) == "light"
    admin_page.goto("/")
    assert theme_of(admin_page) == "light"


def test_no_flash_the_theme_is_set_before_the_page_is_drawn(admin_page: Page) -> None:
    open_appearance(admin_page)
    choose(admin_page, "light")
    admin_page.add_init_script(
        "document.addEventListener('readystatechange', () => {"
        " if (document.documentElement && !window.__first)"
        " window.__first = document.documentElement.getAttribute('data-theme'); });"
    )

    admin_page.goto("/account")

    assert admin_page.evaluate("window.__first") == "light"


def test_page_still_works_when_the_browser_may_not_store(
    page: Page, live_server: LiveServer
) -> None:
    page.add_init_script(
        "Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }});"
    )
    page.goto(f"{live_server.url}/login")

    assert theme_of(page) == "dark"
    expect(page.locator("h1")).to_have_text(t("login.title"))


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("path", PAGES)
def test_both_themes_are_readable_and_fit_the_screen(
    admin_page: Page, theme: str, path: str
) -> None:
    open_appearance(admin_page)
    choose(admin_page, theme)
    admin_page.goto(path)

    ratios = admin_page.evaluate(CONTRAST_JS % repr(SELECTORS).replace("'", '"'))
    for name, ratio in ratios.items():
        if ratio is not None:
            assert ratio >= 4.5, f"{name} in {theme} theme on {path}: contrast {ratio:.2f}"
    assert admin_page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def test_buttons_are_compact(admin_page: Page) -> None:
    admin_page.goto("/settings")
    height = admin_page.locator("main form button[type=submit]").first.bounding_box()["height"]

    assert height <= 40
