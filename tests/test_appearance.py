"""Design basis (issue #73): dark default, light theme, switch on the page "Appearance"."""

import json
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from app.main import create_app
from app.permissions import Permission, route_permission
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
PAGE = "/settings/appearance"
HEX_OR_RGB = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


def nav_of(html: str) -> str:
    return re.search(r"<nav\b.*?</nav>", html, re.DOTALL).group(0)


def tabs_of(html: str) -> str:
    return re.search(r'<nav[^>]*class="settings-tabs".*?</nav>', html, re.DOTALL).group(0)


# --- Test case 1: dark by default ------------------------------------------------------


def test_pages_start_in_the_dark_theme(client: AppClient) -> None:
    for path in ("/", "/articles", PAGE, "/account"):
        assert '<html lang="de" data-theme="dark">' in client.get(path).text


def test_login_page_is_dark_too(anon_client: AppClient) -> None:
    assert 'data-theme="dark"' in anon_client.get("/login").text


# --- Test case 2 + 3 + 5: theme script runs before the page is drawn ------------------


def test_theme_script_is_loaded_in_the_head_without_defer(client: AppClient) -> None:
    html = client.get(PAGE).text
    head = re.search(r"<head>.*?</head>", html, re.DOTALL).group(0)

    tag = re.search(r'<script\b[^>]*src="/static/theme\.js"[^>]*>', head)
    assert tag is not None
    # Without defer it runs before the first paint, so the page does not flash.
    assert "defer" not in tag.group(0) and "async" not in tag.group(0)
    assert head.index("theme.js") < head.index("app.css")


def test_theme_script_stays_dark_when_the_browser_may_not_store(client: AppClient) -> None:
    script = (APP_DIR / "static" / "theme.js").read_text(encoding="utf-8")

    assert "try" in script and "catch" in script
    assert "localStorage" in script
    # Stored only in the browser: no cookie, no request to the server.
    assert "document.cookie" not in script and "fetch(" not in script


# --- Test case 2: the switch is only on the appearance page ----------------------------


def test_appearance_page_has_the_switch(client: AppClient) -> None:
    html = client.get(PAGE).text

    assert DE["appearance.title"] in html
    values = re.findall(r'<input\b[^>]*type="radio"[^>]*name="theme"[^>]*value="([^"]+)"', html)
    assert values == ["dark", "light"]
    assert DE["appearance.theme.dark"] in html and DE["appearance.theme.light"] in html


@pytest.mark.parametrize("path", ["/", "/articles", "/stock", "/account", "/settings"])
def test_no_switch_on_other_pages(client: AppClient, path: str) -> None:
    assert 'name="theme"' not in client.get(path).text


def test_switch_is_not_in_the_navigation(client: AppClient) -> None:
    assert 'name="theme"' not in nav_of(client.get("/").text)


# --- Test case 4: nothing is stored on the server --------------------------------------


def test_appearance_page_has_no_form_to_send() -> None:
    routes = [
        route for route in create_app().routes if isinstance(route, APIRoute) and route.path == PAGE
    ]

    assert [sorted(route.methods) for route in routes] == [["GET"]]


# --- Test case 11 + 12 + 13: rights ----------------------------------------------------


def test_login_is_required(anon_client: AppClient) -> None:
    response = anon_client.get(PAGE, follow_redirects=False)

    assert response.status_code in (302, 303, 307)
    assert "/login" in response.headers["location"] or "/setup" in response.headers["location"]


def test_page_needs_only_the_view_level() -> None:
    app = create_app()
    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path == PAGE)

    assert route_permission(route) == Permission.VIEW


def test_admin_sees_all_tabs_and_appearance_is_marked(client: AppClient) -> None:
    tabs = tabs_of(client.get(PAGE).text)

    for key in ("values", "users", "backups", "log", "appearance"):
        assert DE[f"settings.tab.{key}"] in tabs
    assert re.findall(r'<a\b[^>]*aria-current="page"[^>]*>(.*?)</a>', tabs) == [
        DE["settings.tab.appearance"]
    ]
    assert 'href="/settings"' in nav_of(client.get("/").text)


@pytest.mark.parametrize("role", ["employee", "read_only"])
def test_others_see_the_entry_and_only_the_appearance_tab(role: str, client: AppClient) -> None:
    name = EMPLOYEE_NAME if role == "employee" else READER_NAME
    add_user(name, role)
    with client_as(name) as other:
        start = other.get("/")
        response = other.get(PAGE)

        assert DE["nav.settings"] in nav_of(start.text)
        assert 'href="/settings/appearance"' in nav_of(start.text)
        assert response.status_code == 200
        tabs = tabs_of(response.text)
        assert DE["settings.tab.appearance"] in tabs
        for key in ("values", "users", "backups", "log"):
            assert DE[f"settings.tab.{key}"] not in tabs
        for path in ("/settings", "/settings/users", "/settings/backups", "/settings/log"):
            assert other.get(path).status_code == 403


# --- Test case 8: no fixed colours outside the theme definitions -----------------------


def theme_blocks_removed(css: str) -> str:
    return re.sub(r':root\[data-theme="(?:dark|light)"\]\s*\{.*?\n\}', "", css, flags=re.DOTALL)


def test_both_themes_define_the_same_variables() -> None:
    css = (APP_DIR / "static" / "app.css").read_text(encoding="utf-8")
    blocks = {
        name: re.search(rf':root\[data-theme="{name}"\]\s*\{{(.*?)\n\}}', css, re.DOTALL)
        for name in ("dark", "light")
    }

    assert all(blocks.values())
    names = {
        name: set(re.findall(r"(--mh-[a-z0-9-]+)\s*:", block.group(1)))
        for name, block in blocks.items()
    }
    assert names["dark"] == names["light"] and len(names["dark"]) >= 10


def test_no_fixed_colours_in_the_stylesheet_outside_the_themes() -> None:
    css = (APP_DIR / "static" / "app.css").read_text(encoding="utf-8")

    assert HEX_OR_RGB.findall(theme_blocks_removed(css)) == []


def test_no_fixed_colours_or_inline_styles_in_templates() -> None:
    for template in (APP_DIR / "templates").glob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert HEX_OR_RGB.findall(text) == [], template.name
        assert "style=" not in text, template.name


# --- Translations ----------------------------------------------------------------------


def test_texts_exist_in_both_languages() -> None:
    en = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
    for key in (
        "appearance.title",
        "appearance.theme.dark",
        "appearance.theme.light",
        "appearance.hint",
        "settings.tab.appearance",
    ):
        assert DE[key].strip() and en[key].strip()
