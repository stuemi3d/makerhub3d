"""Template create and edit in pop-ups in a real browser, on desktop and phone (issue #100)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import t
from tests.browser.test_pages import fits_screen_width


def submit(page: Page, key: str) -> None:
    page.locator(f"dialog#popup button[type=submit]:has-text('{t(key)}')").click()


def delete_template(page: Page, name: str) -> None:
    """Removes the template again: the browser tests share one database."""
    page.goto("/templates")
    page.get_by_role("link", name=f"{t('table.action.view')} {name}").click()
    page.get_by_role("link", name=t("template.delete"), exact=True).click()
    page.get_by_role("button", name=t("template.delete.button")).click()
    expect(page.get_by_role("cell", name=name, exact=True)).to_have_count(0)


def test_create_in_a_popup_without_reload_and_errors_stay(
    admin_page: Page, viewport_name: str
) -> None:
    number, name = f"B-TP-{viewport_name}", f"Anker-{viewport_name}"
    admin_page.goto("/templates")
    admin_page.evaluate("window.__stays = true")
    admin_page.get_by_role("button", name=t("templates.new"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    expect(dialog.locator("#number")).to_be_focused()
    assert fits_screen_width(admin_page)

    # An empty name keeps the pop-up open with the error and the typed number.
    dialog.locator("#number").fill(number)
    submit(admin_page, "article.create")
    expect(dialog.locator("[aria-invalid='true']").first).to_be_visible()
    expect(dialog.locator("#number")).to_have_value(number)
    assert fits_screen_width(admin_page)

    dialog.locator("#name").fill(name)
    submit(admin_page, "article.create")
    expect(dialog).not_to_be_visible()
    expect(admin_page.get_by_role("cell", name=number, exact=True)).to_be_visible()
    assert admin_page.evaluate("window.__stays === true"), "the page was reloaded"
    delete_template(admin_page, name)


def test_edit_from_the_list_and_the_detail_and_escape_or_cancel_closes(
    admin_page: Page, viewport_name: str
) -> None:
    number, name = f"B-TE-{viewport_name}", f"Halter-{viewport_name}"
    list_name = f"Liste-neu-{viewport_name}"
    admin_page.goto("/templates/new")
    admin_page.fill("#number", number)
    admin_page.fill("#name", name)
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()
    expect(admin_page.locator(".page-head h1")).to_have_text(name)
    admin_page.evaluate("window.__stays = true")

    # Edit on the detail page: the title is refreshed without a reload.
    admin_page.get_by_role("button", name=t("article.edit"), exact=True).click()
    dialog = admin_page.locator("dialog#popup")
    expect(dialog).to_be_visible()
    expect(dialog.locator("#name")).to_have_value(name)
    assert fits_screen_width(admin_page)
    dialog.locator("#name").fill(f"{name}-neu")
    submit(admin_page, "article.save")
    expect(dialog).not_to_be_visible()
    expect(admin_page.locator(".page-head h1")).to_have_text(f"{name}-neu")
    assert admin_page.evaluate("window.__stays === true"), "the page was reloaded"

    # Cancel closes without saving.
    admin_page.get_by_role("button", name=t("article.edit"), exact=True).click()
    dialog.locator("#name").fill("nicht gespeichert")
    dialog.get_by_role("button", name=t("article.cancel")).click()
    expect(dialog).not_to_be_visible()
    expect(admin_page.locator(".page-head h1")).to_have_text(f"{name}-neu")

    # From the list, with the edit icon.
    admin_page.goto("/templates")
    admin_page.get_by_role("link", name=f"{t('table.action.edit')} {name}-neu").click()
    expect(dialog).to_be_visible()
    expect(dialog.locator("#number")).to_have_value(number)
    dialog.locator("#name").fill(list_name)
    submit(admin_page, "article.save")
    expect(dialog).not_to_be_visible()
    expect(admin_page.get_by_role("cell", name=list_name, exact=True)).to_be_visible()
    delete_template(admin_page, list_name)
