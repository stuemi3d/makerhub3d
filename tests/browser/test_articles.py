"""Articles in a real browser, on desktop and phone (issue #10, test case 14)."""

from playwright.sync_api import Page, expect

from tests.browser.conftest import open_menu, t
from tests.browser.test_pages import fits_screen_width

SECTIONS = ["calculations", "productions", "stock", "sales", "analysis"]


def test_article_list_form_and_detail_work_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    number = f"3D-B-{viewport_name}"
    open_menu(admin_page)
    admin_page.locator("header nav").get_by_text(t("nav.articles"), exact=True).click()
    expect(admin_page.locator("h1")).to_have_text(t("articles.title"))
    assert fits_screen_width(admin_page)

    # The page behind the "new" button (it opens as a pop-up in the list, see test_popups.py).
    admin_page.goto("/articles/new")
    assert fits_screen_width(admin_page)
    admin_page.fill("#number", number)
    admin_page.fill("#name", "Kabelhalter mit einem ziemlich langen Namen zum Testen der Breite")
    admin_page.fill("#selling_price", "12,90")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()

    expect(admin_page.locator("h1")).to_contain_text("Kabelhalter")
    assert fits_screen_width(admin_page)
    for section in SECTIONS:
        expect(admin_page.locator("a.tile", has_text=t(f"article.tile.{section}"))).to_be_visible()

    admin_page.locator("a.tile", has_text=t("article.tile.stock")).click()
    expect(admin_page.get_by_text(t("article.section.empty"))).to_be_visible()
    assert fits_screen_width(admin_page)

    # The path at the top leads back: first to the article, then to the list.
    crumbs = admin_page.get_by_label("breadcrumb")
    crumbs.get_by_role("link", name=number).click()
    expect(admin_page.locator("h1")).to_contain_text("Kabelhalter")
    admin_page.get_by_label("breadcrumb").get_by_role("link", name=t("articles.title")).click()
    expect(admin_page.get_by_text(number)).to_be_visible()
    assert fits_screen_width(admin_page)


# --- Issue #52: operation and orientation, stage 1 --------------------------------------------


def test_article_detail_card_and_compact_buttons_on_every_screen(
    admin_page: Page, viewport_name: str
) -> None:
    admin_page.goto("/articles/new")
    admin_page.fill("#number", f"3D-C-{viewport_name}")
    admin_page.fill("#name", "Kabelhalter")
    admin_page.fill("#selling_price", "12,90")
    admin_page.locator(f"button[type=submit]:has-text('{t('article.create')}')").click()

    card = admin_page.locator("article.detail-card")
    expect(card).to_be_visible()
    first, second = card.locator("dl > div").nth(0), card.locator("dl > div").nth(1)
    box1, box2 = first.bounding_box(), second.bounding_box()
    if viewport_name == "phone":
        assert abs(box1["x"] - box2["x"]) < 2, "on the phone the fields stand below each other"
    else:
        assert box2["x"] > box1["x"] + 50, "on the desktop the fields use two columns"
    expect(card.locator(".badge")).to_have_text(t("article.status.active"))

    heading = admin_page.locator("h1").bounding_box()
    edit = admin_page.get_by_role("button", name=t("article.edit")).bounding_box()
    assert edit["height"] < 40, "buttons are compact"
    if viewport_name != "phone":
        assert abs((edit["y"] + edit["height"] / 2) - (heading["y"] + heading["height"] / 2)) < 30
    assert fits_screen_width(admin_page)


def test_tabs_and_new_button_on_every_screen(admin_page: Page, viewport_name: str) -> None:
    admin_page.goto("/articles")
    tabs = admin_page.get_by_label(t("area.tabs"))
    expect(tabs.get_by_role("link", name=t("articles.title"))).to_have_attribute(
        "aria-current", "page"
    )
    tabs.get_by_role("link", name=t("templates.title")).click()
    expect(admin_page.locator("h1")).to_have_text(t("templates.title"))
    tabs = admin_page.get_by_label(t("area.tabs"))
    expect(tabs.get_by_role("link", name=t("templates.title"))).to_have_attribute(
        "aria-current", "page"
    )
    tabs.get_by_role("link", name=t("attributes.title")).click()
    expect(admin_page.locator("h1")).to_have_text(t("attributes.title"))

    new = admin_page.get_by_role("button", name=t("attributes.new"), exact=True)
    assert new.bounding_box()["height"] < 40, "buttons are compact"
    expect(admin_page.locator("article.empty-hint")).to_contain_text(t("attributes.empty"))
    assert fits_screen_width(admin_page)
