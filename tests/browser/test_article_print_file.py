"""The .gcode.3mf file of an article in a browser, on desktop and phone (issue #268, case 5)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width
from tests.test_slice_reader import sliced_3mf

SUBMIT = f"button[type=submit]:has-text('{t('article.create')}')"


def test_file_chosen_in_the_new_article_popup_is_shown_with_download_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-PF-{viewport_name}"
    admin_page.goto("/articles")
    admin_page.get_by_role("button", name=t("articles.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()

    field = dialog.locator("input#print_file")
    field.scroll_into_view_if_needed()
    expect(field).to_be_visible()
    box = field.bounding_box()
    width = admin_page.viewport_size["width"]
    assert box["x"] >= 0 and box["x"] + box["width"] <= width, "the field fits the screen"

    dialog.locator("#number").fill(number)
    dialog.locator("#name").fill("Halter mit Druckdatei")
    field.set_input_files(
        {
            "name": "ein-recht-langer-dateiname-fuer-den-halter.gcode.3mf",
            "mimeType": "application/octet-stream",
            "buffer": sliced_3mf(),
        }
    )
    dialog.locator(SUBMIT).click()
    expect(dialog).not_to_be_visible()

    admin_page.locator("table.data-table tbody tr").filter(has_text=number).get_by_role(
        "link", name=t("table.action.view")
    ).click()
    expect(admin_page.locator(".print-file-name")).to_have_text(
        "ein-recht-langer-dateiname-fuer-den-halter.gcode.3mf"
    )
    link = admin_page.locator("#article-file-download")
    expect(link).to_be_visible()
    with admin_page.expect_download() as download:
        link.click()
    assert download.value.suggested_filename.endswith(".gcode.3mf")
    assert fits_screen_width(admin_page)


def test_wrong_file_shows_the_message_in_the_popup(admin_page: Page, viewport_name: str) -> None:
    admin_page.goto("/articles")
    admin_page.get_by_role("button", name=t("articles.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    dialog.locator("#number").fill(f"3D-PFF-{viewport_name}")
    dialog.locator("#name").fill("Falsche Datei")
    dialog.locator("input#print_file").set_input_files(
        {"name": "wuerfel.gcode.3mf", "mimeType": "application/octet-stream", "buffer": b"x"}
    )
    dialog.locator(SUBMIT).click()
    expect(dialog.locator("#print_file-error")).to_have_text(t("calc.file.error.invalid"))
    expect(dialog).to_be_visible()
