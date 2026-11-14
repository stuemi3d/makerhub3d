"""Variants in pop-ups in a real browser, on desktop and phone (issue #87, test case 29)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def submit(page: Page, key: str) -> None:
    page.locator(f"button[type=submit]:has-text('{t(key)}')").click()


def make_template(page: Page, viewport_name: str, tag: str) -> tuple[str, str]:
    attribute = f"Stoff-{tag}-{viewport_name}"
    prefix = f"B-P-{viewport_name}-{tag}"
    page.goto("/attributes/new")
    page.fill("#name", attribute)
    page.fill("#values", "rot = rt\nblau = bl")
    submit(page, "article.create")
    page.goto("/templates/new")
    page.fill("#number", prefix)
    page.fill("#name", f"Halter-{viewport_name}-{tag}")
    page.get_by_label(attribute).check()
    submit(page, "article.create")
    expect(page.locator("h1")).to_have_text(f"Halter-{viewport_name}-{tag}")
    return attribute, prefix


def test_single_variant_in_a_popup_with_number_suggestion_and_no_reload(
    admin_page: Page, viewport_name: str
) -> None:
    _attribute, prefix = make_template(admin_page, viewport_name, "one")
    admin_page.evaluate("window.__stays = true")

    admin_page.get_by_role("button", name=t("variant.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    expect(dialog.locator("#number")).to_be_focused()
    # The suggestion works in the pop-up, and typed input is never overwritten.
    dialog.locator("select[name^=attr_]").select_option(label="rot")
    expect(dialog.locator("#number")).to_have_value(f"{prefix}-rt")
    dialog.locator("select[name^=attr_]").select_option(label="blau")
    expect(dialog.locator("#number")).to_have_value(f"{prefix}-bl")
    dialog.locator("#name").fill("Blauer Halter")
    submit(admin_page, "article.create")

    expect(dialog).not_to_be_visible()
    expect(admin_page.get_by_role("link", name=f"{prefix}-bl", exact=True)).to_be_visible()
    assert admin_page.evaluate("window.__stays === true"), "the page was reloaded"
    assert fits_screen_width(admin_page)

    # Opening again after saving: the suggestion still works (the script is set up again).
    admin_page.get_by_role("button", name=t("variant.new"), exact=True).click()
    dialog.locator("select[name^=attr_]").select_option(label="rot")
    expect(dialog.locator("#number")).to_have_value(f"{prefix}-rt")
    dialog.locator("#number").fill("EIGENE-1")
    dialog.locator("select[name^=attr_]").select_option(label="blau")
    expect(dialog.locator("#number")).to_have_value("EIGENE-1")


def test_errors_stay_and_only_cancel_closes_the_popup(admin_page: Page, viewport_name: str) -> None:
    make_template(admin_page, viewport_name, "err")

    admin_page.get_by_role("button", name=t("variant.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    dialog.locator("#name").fill("Ohne Nummer")
    submit(admin_page, "article.create")

    expect(dialog.locator("[aria-invalid='true']").first).to_be_visible()
    expect(dialog.locator("#name")).to_have_value("Ohne Nummer")
    admin_page.keyboard.press("Escape")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()


def test_all_combinations_in_a_popup_fit_the_screen(admin_page: Page, viewport_name: str) -> None:
    _attribute, prefix = make_template(admin_page, viewport_name, "all")
    admin_page.evaluate("window.__stays = true")

    admin_page.get_by_role("button", name=t("variant.bulk.open"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    box = dialog.bounding_box()
    viewport = admin_page.viewport_size
    assert box is not None and viewport is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"]
    assert box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"]
    expect(dialog.locator("input[name^=number_]")).to_have_count(2)
    dialog.locator("#number_0").fill(f"{prefix}-a")
    dialog.locator("#number_1").fill(f"{prefix}-b")
    submit(admin_page, "variant.bulk.create")

    expect(dialog).not_to_be_visible()
    expect(admin_page.get_by_role("link", name=f"{prefix}-a", exact=True)).to_be_visible()
    expect(admin_page.get_by_role("link", name=f"{prefix}-b", exact=True)).to_be_visible()
    assert admin_page.evaluate("window.__stays === true"), "the page was reloaded"

    admin_page.get_by_role("button", name=t("variant.bulk.open"), exact=True).click()
    expect(dialog.get_by_text(t("variant.bulk.nothing_new"))).to_be_visible()
    expect(dialog.locator("button[type=submit]")).to_have_count(0)
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()


def test_suggestion_still_works_after_an_error_and_focus_skips_existing_rows(
    admin_page: Page, viewport_name: str
) -> None:
    _attribute, prefix = make_template(admin_page, viewport_name, "sug")
    dialog = admin_page.locator("dialog#popup")

    admin_page.get_by_role("button", name=t("variant.new"), exact=True).click()
    dialog.locator("select[name^=attr_]").select_option(label="rot")
    expect(dialog.locator("#number")).to_have_value(f"{prefix}-rt")
    dialog.locator("#selling_price").fill("kein Preis")
    submit(admin_page, "article.create")
    expect(dialog.locator("#selling_price[aria-invalid='true']")).to_be_visible()
    # The suggested number came back with the form; a new choice still changes it. (The form
    # is set up a moment after it appears; a person is never that fast, a test is.)
    expect(dialog.locator("#number[data-suggesting]")).to_be_attached()
    dialog.locator("select[name^=attr_]").select_option(label="blau")
    expect(dialog.locator("#number")).to_have_value(f"{prefix}-bl")
    dialog.locator("#selling_price").fill("")
    submit(admin_page, "article.create")
    expect(dialog).not_to_be_visible()

    # The first combination exists now: the focus goes to the first free number field.
    admin_page.get_by_role("button", name=t("variant.bulk.open"), exact=True).click()
    expect(dialog.locator("input[name^=number_]")).to_have_count(1)
    expect(dialog.locator("input[name^=number_]")).to_be_focused()
