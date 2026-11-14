"""Operation and orientation, stage 1 (issue #52). All data is invented.

Test case numbers refer to the confirmed list in the issue.
"""

import json
import re
from pathlib import Path

import pytest

from tests.auth import READER_NAME, AppClient, add_user, client_as

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))


def make_article(client: AppClient, number: str = "3D-0001", name: str = "Kabelhalter") -> int:
    response = client.post(
        "/articles/new",
        data={
            "number": number,
            "name": name,
            "selling_price": "12,90",
            "weight": "",
            "min_stock": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].split("/")[2].split("?")[0])


def tabs(html: str) -> str | None:
    match = re.search(r'<nav[^>]*class="area-tabs".*?</nav>', html, re.DOTALL)
    return match.group(0) if match else None


def current_tab(html: str) -> list[str]:
    block = tabs(html) or ""
    return re.findall(r'<a href="[^"]*" aria-current="page">(.*?)</a>', block)


def crumbs(html: str) -> str | None:
    match = re.search(r'<nav[^>]*aria-label="breadcrumb".*?</nav>', html, re.DOTALL)
    return match.group(0) if match else None


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


# --- Test cases 1-4: article detail as a card ---------------------------------------------


def test_article_detail_shows_the_fields_in_a_card(client: AppClient) -> None:
    article_id = make_article(client)
    html = client.get(f"/articles/{article_id}").text

    card = re.search(r'<article class="detail-card">(.*?)</article>', html, re.DOTALL)
    assert card, "the fields must be inside a card"
    for text in ("3D-0001", "Kabelhalter", "12,90", DE["article.field.number"]):
        assert text in card.group(1), text
    for label in ("selling_price", "weight", "min_stock"):
        assert DE[f"article.field.{label}"] in card.group(1)


def test_status_is_a_coloured_label(client: AppClient) -> None:
    article_id = make_article(client)
    html = client.get(f"/articles/{article_id}").text
    assert re.search(rf'<span class="chip chip-active">{DE["article.status.active"]}</span>', html)

    client.post(f"/articles/{article_id}/deactivate", data={})
    html = client.get(f"/articles/{article_id}").text
    assert re.search(
        rf'<span class="chip chip-inactive">{DE["article.status.inactive"]}</span>', html
    )


def test_buttons_stand_next_to_the_heading_for_editors_only(
    client: AppClient, reader: AppClient
) -> None:
    article_id = make_article(client)

    head = re.search(
        r'<div class="page-head">.*?</h1>.*?</div>',
        client.get(f"/articles/{article_id}").text,
        re.DOTALL,
    )
    assert head
    block = head.group(0)
    assert "<h1>" in block
    assert f"/articles/{article_id}/edit" in block
    assert f"/articles/{article_id}/deactivate" in block

    reader_html = reader.get(f"/articles/{article_id}").text
    assert f"/articles/{article_id}/edit" not in reader_html
    assert f"/articles/{article_id}/deactivate" not in reader_html


def test_tiles_still_lead_to_their_pages(client: AppClient) -> None:
    article_id = make_article(client)
    html = client.get(f"/articles/{article_id}").text
    for section in ("calculations", "productions", "stock", "sales", "analysis"):
        assert f'href="/articles/{article_id}/{section}"' in html
        assert client.get(f"/articles/{article_id}/{section}").status_code == 200


# --- Test cases 6-8: tabs, breadcrumb path, no more "back" links ---------------------------


@pytest.mark.parametrize(
    ("path", "active"),
    [
        ("/articles", "articles.title"),
        ("/articles/new", "articles.title"),
        ("/templates", "templates.title"),
        ("/templates/new", "templates.title"),
        ("/attributes", "attributes.title"),
        ("/attributes/new", "attributes.title"),
    ],
)
def test_area_tabs_mark_the_current_area(client: AppClient, path: str, active: str) -> None:
    html = client.get(path).text

    block = tabs(html)
    assert block, "the tabs must be on the page"
    for key, href in (
        ("articles.title", "/articles"),
        ("templates.title", "/templates"),
        ("attributes.title", "/attributes"),
    ):
        assert f'href="{href}"' in block
        assert DE[key] in block
    assert current_tab(html) == [DE[active]]


def test_area_tabs_are_also_on_detail_pages(client: AppClient) -> None:
    article_id = make_article(client)
    assert current_tab(client.get(f"/articles/{article_id}").text) == [DE["articles.title"]]
    assert current_tab(client.get(f"/articles/{article_id}/stock").text) == [DE["articles.title"]]

    client.post(
        "/templates/new",
        data={
            "number": "V-1",
            "name": "Halter A",
            "description": "",
            "selling_price": "",
            "weight": "",
            "min_stock": "",
        },
    )
    assert current_tab(client.get("/templates/1").text) == [DE["templates.title"]]


def test_other_pages_have_no_area_tabs(client: AppClient) -> None:
    for path in ("/", "/settings", "/account"):
        assert tabs(client.get(path).text) is None, path


def test_article_detail_has_a_path_back_to_the_list(client: AppClient) -> None:
    article_id = make_article(client)
    block = crumbs(client.get(f"/articles/{article_id}").text)

    assert block
    assert 'href="/articles"' in block
    assert DE["articles.title"] in block
    assert "3D-0001" in block


def test_article_section_path_leads_back_to_the_article(client: AppClient) -> None:
    article_id = make_article(client)
    block = crumbs(client.get(f"/articles/{article_id}/stock").text)

    assert block
    assert 'href="/articles"' in block
    assert f'href="/articles/{article_id}"' in block
    assert DE["article.tile.stock"] in block


def test_template_detail_has_a_path_back_to_the_list(client: AppClient) -> None:
    client.post(
        "/templates/new",
        data={
            "number": "V-1",
            "name": "Halter A",
            "description": "",
            "selling_price": "",
            "weight": "",
            "min_stock": "",
        },
    )
    block = crumbs(client.get("/templates/1").text)

    assert block
    assert 'href="/templates"' in block
    assert "Halter A" in block


def test_back_links_at_the_page_end_are_gone(client: AppClient) -> None:
    article_id = make_article(client)
    client.post(
        "/templates/new",
        data={
            "number": "V-1",
            "name": "Halter A",
            "description": "",
            "selling_price": "",
            "weight": "",
            "min_stock": "",
        },
    )
    for key in ("article.back", "article.back_to_list", "template.back_to_list"):
        assert key not in DE, f"the unused text {key} must be removed"
    pages = [
        "/articles",
        f"/articles/{article_id}",
        f"/articles/{article_id}/stock",
        "/templates",
        "/templates/1",
        "/attributes",
    ]
    for path in pages:
        html = client.get(path).text
        assert "Zurück zur" not in html, path
        assert "Zurück zum" not in html, path


# --- Test cases 9-11: lists --------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "new_key", "new_href"),
    [
        ("/articles", "articles.new", "/articles/new"),
        ("/templates", "templates.new", "/templates/new"),
        ("/attributes", "attributes.new", "/attributes/new"),
    ],
)
def test_new_button_is_in_the_heading_row(
    client: AppClient, path: str, new_key: str, new_href: str
) -> None:
    html = client.get(path).text

    head = re.search(r'<div class="page-head">.*?</h1>.*?</div>', html, re.DOTALL)
    assert head, "heading and buttons belong in one row"
    assert f'href="{new_href}"' in head.group(0)
    assert DE[new_key] in head.group(0)


def test_inactive_toggle_sits_next_to_the_button_and_still_works(client: AppClient) -> None:
    article_id = make_article(client)
    client.post(f"/articles/{article_id}/deactivate", data={})

    html = client.get("/articles").text
    head = re.search(r'<div class="page-head">.*?</h1>.*?</div>', html, re.DOTALL)
    assert head
    assert 'href="/articles?inactive=1"' in head.group(0)
    assert "3D-0001" not in html

    html = client.get("/articles?inactive=1").text
    assert "3D-0001" in html
    assert 'href="/articles"' in re.search(
        r'<div class="page-head">.*?</h1>.*?</div>', html, re.DOTALL
    ).group(0)


@pytest.mark.parametrize(
    ("path", "empty_key", "create_key", "create_href"),
    [
        ("/articles", "articles.empty", "articles.empty.create", "/articles/new"),
        ("/templates", "templates.empty", "templates.empty.create", "/templates/new"),
        ("/attributes", "attributes.empty", "attributes.empty.create", "/attributes/new"),
    ],
)
def test_empty_list_shows_a_hint_with_a_link_to_create(
    client: AppClient, path: str, empty_key: str, create_key: str, create_href: str
) -> None:
    html = client.get(path).text

    assert DE[empty_key] in html
    empty = re.search(r'<article class="empty-hint">(.*?)</article>', html, re.DOTALL)
    assert empty, "the hint belongs in its own box"
    assert DE[create_key] in empty.group(1)
    assert f'href="{create_href}"' in empty.group(1)


@pytest.mark.parametrize(
    ("path", "empty_key", "create_key"),
    [
        ("/articles", "articles.empty", "articles.empty.create"),
        ("/templates", "templates.empty", "templates.empty.create"),
        ("/attributes", "attributes.empty", "attributes.empty.create"),
    ],
)
def test_reader_sees_only_the_hint_without_a_create_link(
    reader: AppClient, path: str, empty_key: str, create_key: str
) -> None:
    html = reader.get(path).text

    assert DE[empty_key] in html
    assert DE[create_key] not in html
    assert 'href="/articles/new"' not in html


# --- Test case 12: translations ---------------------------------------------------------


def test_new_texts_exist_in_german_and_english() -> None:
    for key in (
        "articles.empty.create",
        "templates.empty.create",
        "attributes.empty.create",
        "area.tabs",
    ):
        assert DE.get(key), key
        assert EN.get(key), key
    assert DE.keys() == EN.keys()
