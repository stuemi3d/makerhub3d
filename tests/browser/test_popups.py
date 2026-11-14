"""Pop-ups in a real browser, on desktop and phone (issue #76, test cases 1-4 and 11-14)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width

SUBMIT = f"button[type=submit]:has-text('{t('article.create')}')"
SAVE = f"button[type=submit]:has-text('{t('article.save')}')"


def add_in_popup(page: Page, number: str, name: str) -> None:
    page.goto("/articles")
    page.get_by_role("button", name=t("articles.new"), exact=True).click()
    expect(page.locator("dialog#popup[open]")).to_be_visible()
    page.fill("dialog #number", number)
    page.fill("dialog #name", name)
    page.locator("dialog " + SUBMIT).click()
    expect(page.locator("dialog#popup")).not_to_be_visible()


def row_of(page: Page, number: str):
    return page.locator("table.data-table tbody tr").filter(has_text=number)


def test_new_article_in_a_popup_appears_in_the_list_without_reload(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-P-{viewport_name}-1"
    admin_page.goto("/articles")
    marker = "window.__stays = true"
    admin_page.evaluate(marker)

    admin_page.get_by_role("button", name=t("articles.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    expect(dialog.locator("h1")).to_have_text(t("article.new.title"))
    # The list stays behind the pop-up and the address does not change (case 1, 3).
    expect(admin_page.locator("table.data-table, .empty-hint").first).to_be_attached()
    assert admin_page.url.endswith("/articles")
    expect(dialog.locator("#number")).to_be_focused()
    admin_page.keyboard.type(number)
    admin_page.keyboard.press("Tab")
    admin_page.keyboard.type("Popup-Halter")
    admin_page.keyboard.press("Enter")

    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, number)).to_contain_text("Popup-Halter")
    assert admin_page.url.endswith("/articles")
    assert admin_page.evaluate("window.__stays === true"), "the page was reloaded"


def test_edit_in_a_popup_updates_the_row(admin_page: Page, viewport_name: str) -> None:
    number = f"3D-P-{viewport_name}-2"
    add_in_popup(admin_page, number, "Vorher")

    row_of(admin_page, number).get_by_role("link", name=t("table.action.edit")).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog.locator("h1")).to_have_text(t("article.edit.title"))
    expect(dialog.locator("#name")).to_have_value("Vorher")
    dialog.locator("#name").fill("Nachher")
    dialog.locator("#selling_price").fill("19,90")
    dialog.locator(SAVE).click()

    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, number)).to_contain_text("Nachher")
    expect(row_of(admin_page, number)).to_contain_text("19,90")
    # The sort order chosen before stays after saving.
    admin_page.goto("/articles?sort=name&dir=desc")
    row_of(admin_page, number).get_by_role("link", name=t("table.action.edit")).click()
    admin_page.locator("dialog #name").fill("Nachher 2")
    admin_page.locator("dialog " + SAVE).click()
    expect(row_of(admin_page, number)).to_contain_text("Nachher 2")
    assert "sort=name" in admin_page.url


def test_errors_are_shown_in_the_popup_and_input_stays(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-P-{viewport_name}-3"
    add_in_popup(admin_page, number, "Erster")

    admin_page.get_by_role("button", name=t("articles.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    dialog.locator("#number").fill(number)
    dialog.locator("#name").fill("Zweiter")
    dialog.locator(SUBMIT).click()

    expect(dialog).to_be_visible()
    expect(dialog.get_by_text(t("article.error.taken"))).to_be_visible()
    expect(dialog.locator("#name")).to_have_value("Zweiter")
    expect(dialog.locator("[aria-invalid=true]").first).to_be_focused()
    # Fix the number: the same pop-up saves now.
    dialog.locator("#number").fill(number + "-B")
    dialog.locator(SUBMIT).click()
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, number + "-B")).to_be_visible()


def test_escape_cancel_and_backdrop_close_without_saving_and_return_focus(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-P-{viewport_name}-4"
    add_in_popup(admin_page, number, "Bleibt")
    dialog = admin_page.locator("dialog#popup")

    def open_edit():
        link = row_of(admin_page, number).get_by_role("link", name=t("table.action.edit"))
        link.click()
        expect(dialog).to_be_visible()
        dialog.locator("#name").fill("Geändert")
        return link

    link = open_edit()
    admin_page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(link).to_be_focused()
    expect(row_of(admin_page, number)).to_contain_text("Bleibt")

    open_edit()
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, number)).to_contain_text("Bleibt")

    open_edit()
    admin_page.mouse.click(2, 2)  # outside the pop-up box: on the dimmed background
    expect(dialog).not_to_be_visible()
    expect(row_of(admin_page, number)).to_contain_text("Bleibt")

    # Nothing was typed into the stale form: opening again shows the stored value.
    row_of(admin_page, number).get_by_role("link", name=t("table.action.edit")).click()
    expect(dialog.locator("#name")).to_have_value("Bleibt")


def test_tab_stays_inside_the_popup(admin_page: Page) -> None:
    admin_page.goto("/articles")
    admin_page.get_by_role("button", name=t("articles.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    for _ in range(12):
        admin_page.keyboard.press("Tab")
        # Focus may leave the page for the browser's own bar, but never reach the list behind.
        assert admin_page.evaluate("!document.activeElement.closest('header, main')")


def test_popup_fits_the_screen_and_scrolls_on_the_phone(
    admin_page: Page, viewport_name: str
) -> None:
    admin_page.goto("/articles")
    admin_page.get_by_role("button", name=t("articles.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    assert fits_screen_width(admin_page)
    box = dialog.bounding_box()
    assert box is not None
    viewport = admin_page.viewport_size
    assert viewport is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"]
    assert box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"]
    if viewport_name == "phone":
        assert box["width"] >= viewport["width"] - 24
        # A short screen: the content may scroll inside the pop-up, the buttons stay reachable.
        admin_page.set_viewport_size({"width": 390, "height": 360})
        admin_page.locator("dialog " + SUBMIT).scroll_into_view_if_needed()
        expect(admin_page.locator("dialog " + SUBMIT)).to_be_in_viewport()
        assert fits_screen_width(admin_page)


def test_the_edit_link_still_opens_the_normal_page(admin_page: Page, viewport_name: str) -> None:
    number = f"3D-P-{viewport_name}-5"
    add_in_popup(admin_page, number, "Normal")
    link = row_of(admin_page, number).get_by_role("link", name=t("table.action.edit"))
    href = link.get_attribute("href")
    assert href and href.endswith("/edit")

    # "Open in a new tab" (Ctrl+click) gives the complete page, not a pop-up.
    with admin_page.context.expect_page() as opened:
        link.click(modifiers=["Control"])
    new_tab = opened.value
    new_tab.wait_for_load_state()
    expect(new_tab.locator("header nav")).to_be_visible()
    expect(new_tab.locator("h1")).to_have_text(t("article.edit.title"))
    expect(new_tab.locator("dialog[open]")).to_have_count(0)
    new_tab.close()
    expect(admin_page.locator("dialog#popup")).not_to_be_visible()
