"""Top navigation bar (issue #74). All data is invented.

Test case numbers refer to the confirmed list in the issue.
"""

import json
import re
from pathlib import Path

from tests.auth import READER_NAME, AppClient, add_user, client_as

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))


def header(html: str) -> str:
    match = re.search(r"<header\b.*?</header>", html, re.DOTALL)
    assert match, "page has no header"
    return match.group(0)


# --- Test case 1: bar with name, entries and user ---------------------------------------


def test_bar_has_name_entries_and_user_in_this_order(client: AppClient) -> None:
    bar = header(client.get("/").text)

    positions = [
        bar.index("MakerHub 3D"),
        bar.index(DE["nav.articles"]),
        bar.index(DE["nav.settings"]),
        bar.index(DE["nav.logout"]),
    ]
    assert positions == sorted(positions)
    assert 'action="/logout"' in bar


def test_bar_spans_the_full_width(client: AppClient) -> None:
    html = client.get("/articles").text

    assert re.search(r'<header class="[^"]*container-fluid', html)
    assert re.search(r'<main class="container-fluid', html)


# --- Test case 2: current entry ---------------------------------------------------------


def test_articles_entry_is_current_on_list_and_detail_pages(client: AppClient) -> None:
    response = client.post(
        "/articles/new",
        data={"number": "3D-0001", "name": "Halter", "selling_price": "", "weight": ""},
        follow_redirects=False,
    )
    article_url = response.headers["location"].split("?")[0]
    for path in ("/articles", article_url):
        bar = header(client.get(path).text)
        current = re.findall(r'<a\b[^>]*aria-current="page"[^>]*>(.*?)</a>', bar)
        assert current == [DE["nav.articles"]], path


# --- Test case 3: unbuilt entries -------------------------------------------------------


def test_unbuilt_entries_are_greyed_out_and_not_links(client: AppClient) -> None:
    bar = header(client.get("/").text)

    disabled = re.findall(r'<a\b[^>]*aria-disabled="true"[^>]*>(.*?)</a>', bar)
    assert disabled == [DE[k] for k in ("nav.calculation", "nav.production", "nav.analysis")]
    assert not re.search(r"<a\b[^>]*aria-disabled[^>]*href=", bar)


# --- Test case 4: permissions unchanged -------------------------------------------------


def test_reader_sees_the_same_entries_as_before_and_settings_goes_to_appearance(
    client: AppClient,
) -> None:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        bar = header(reader.get("/").text)
    assert 'href="/settings/appearance"' in bar
    assert DE["nav.stock"] in bar


# --- Test case 5: no entries without login / while password must change ------------------


def test_login_page_bar_has_no_entries_and_no_menu_button(client: AppClient) -> None:
    anonymous = AppClient(client.app)
    bar = header(anonymous.get("/login").text)

    assert "MakerHub 3D" in bar
    assert "<a " not in bar
    assert "nav-toggle" not in bar


# --- Test case 6: switch is not in the bar ----------------------------------------------


def test_theme_switch_is_not_in_the_bar_but_in_appearance_settings(client: AppClient) -> None:
    assert 'name="theme"' not in header(client.get("/").text)
    assert 'name="theme"' in client.get("/settings/appearance").text


# --- Test cases 8-10: menu button markup -------------------------------------------------


def test_menu_button_controls_the_menu(client: AppClient) -> None:
    bar = header(client.get("/").text)

    button = re.search(r'<button\b[^>]*class="nav-toggle[^"]*"[^>]*>', bar)
    assert button
    assert 'aria-expanded="false"' in button.group(0)
    assert 'aria-controls="main-menu"' in button.group(0)
    assert 'id="main-menu"' in bar
