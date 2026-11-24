"""The note of an article in a real browser, on desktop and phone (issue #266, test case 15)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width

SUBMIT = f"button[type=submit]:has-text('{t('article.create')}')"


def test_note_field_in_the_new_article_popup_is_usable_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-N-{viewport_name}"
    admin_page.goto("/articles")
    admin_page.get_by_role("button", name=t("articles.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()

    note = dialog.locator("textarea#note")
    note.scroll_into_view_if_needed()
    expect(note).to_be_visible()
    box = note.bounding_box()
    width = admin_page.viewport_size["width"]
    assert box["x"] >= 0 and box["x"] + box["width"] <= width, "the field fits the screen"
    assert fits_screen_width(admin_page)

    dialog.locator("#number").fill(number)
    dialog.locator("#name").fill("Halter mit Notiz")
    note.fill("Erste Zeile\nZweite Zeile")
    dialog.locator(SUBMIT).click()
    expect(dialog).not_to_be_visible()

    admin_page.locator("table.data-table tbody tr").filter(has_text=number).get_by_role(
        "link", name=t("table.action.view")
    ).click()
    shown = admin_page.locator("dd.article-note")
    expect(shown).to_have_text("Erste Zeile\nZweite Zeile")
    lines = shown.bounding_box()["height"]
    assert lines > 30, "the line break is visible"
    assert fits_screen_width(admin_page)
