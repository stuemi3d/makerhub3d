"""The image of an article in a real browser, on desktop and phone (issue #267, test case 5)."""

import struct
import zlib

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width

SUBMIT = f"button[type=submit]:has-text('{t('article.create')}')"


def wide_png(width: int = 1600, height: int = 900) -> bytes:
    """A real, plain grey PNG that is wider than any phone and most desktop pages."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = (b"\x00" + b"\x80" * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def test_image_chosen_in_the_new_article_popup_is_shown_and_fits_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-IMG-{viewport_name}"
    admin_page.goto("/articles")
    admin_page.get_by_role("button", name=t("articles.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()

    field = dialog.locator("input#image")
    field.scroll_into_view_if_needed()
    expect(field).to_be_visible()
    box = field.bounding_box()
    width = admin_page.viewport_size["width"]
    assert box["x"] >= 0 and box["x"] + box["width"] <= width, "the field fits the screen"

    dialog.locator("#number").fill(number)
    dialog.locator("#name").fill("Halter mit Bild")
    field.set_input_files({"name": "halter.png", "mimeType": "image/png", "buffer": wide_png()})
    dialog.locator(SUBMIT).click()
    expect(dialog).not_to_be_visible()

    admin_page.locator("table.data-table tbody tr").filter(has_text=number).get_by_role(
        "link", name=t("table.action.view")
    ).click()
    image = admin_page.locator("img.article-image")
    expect(image).to_be_visible()
    admin_page.wait_for_function("document.querySelector('img.article-image').naturalWidth > 0")
    shown = image.bounding_box()
    assert shown["x"] >= 0 and shown["x"] + shown["width"] <= width, "the image fits the screen"
    assert shown["height"] <= 400, "the image is not shown huge"
    assert fits_screen_width(admin_page)


def test_wrong_file_shows_the_message_in_the_popup(admin_page: Page, viewport_name: str) -> None:
    admin_page.goto("/articles")
    admin_page.get_by_role("button", name=t("articles.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    dialog.locator("#number").fill(f"3D-IMGF-{viewport_name}")
    dialog.locator("#name").fill("Falsches Bild")
    dialog.locator("input#image").set_input_files(
        {"name": "bild.png", "mimeType": "image/png", "buffer": b"<svg></svg>"}
    )
    dialog.locator(SUBMIT).click()
    expect(dialog.locator("#image-error")).to_have_text(t("article.error.image_type"))
    expect(dialog).to_be_visible()
