"""Base layout: navigation, dark theme, translations and embedding rules."""

import json
import re
from contextlib import AbstractContextManager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings, parse_embed_origins
from app.main import create_app
from tests.auth import AppClient, signed_in_client

APP_DIR = Path(__file__).resolve().parent.parent / "app"
NAV_KEYS = [
    "nav.dashboard",
    "nav.articles",
    "nav.calculation",
    "nav.production",
    "nav.stock",
    "nav.analysis",
    "nav.settings",
]


def locale(name: str) -> dict[str, str]:
    return json.loads((APP_DIR / "locales" / f"{name}.json").read_text(encoding="utf-8"))


def make_client(monkeypatch: pytest.MonkeyPatch, **env: str) -> AbstractContextManager[AppClient]:
    """A logged-in client for an app started with the given environment variables."""
    for name in ("MAKERHUB_LANGUAGE", "MAKERHUB_EMBED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return signed_in_client()


# --- Test case 1: shared frame ---------------------------------------------------------


def test_start_page_uses_shared_frame(client: TestClient) -> None:
    html = client.get("/").text

    assert re.search(r"<header\b", html)
    assert re.search(r"<nav\b", html)
    assert re.search(r"<main\b", html)
    assert "MakerHub 3D" in re.search(r"<header\b.*?</header>", html, re.DOTALL).group(0)


# --- Test case 2: navigation -----------------------------------------------------------


def test_navigation_contains_all_entries_from_the_plan(client: TestClient) -> None:
    html = client.get("/").text
    nav = re.search(r"<nav\b.*?</nav>", html, re.DOTALL).group(0)
    labels = [locale("de")[key] for key in NAV_KEYS]

    positions = [nav.index(label) for label in labels]
    assert positions == sorted(positions)


def test_dashboard_is_marked_as_current_and_unbuilt_entries_are_not_links_yet(
    client: TestClient,
) -> None:
    nav = re.search(r"<nav\b.*?</nav>", client.get("/").text, re.DOTALL).group(0)

    current = re.findall(r'<a\b[^>]*aria-current="page"[^>]*>', nav)
    assert len(current) == 1
    assert 'href="/"' in current[0]
    # Built so far: dashboard, articles, stock and settings.
    assert len(re.findall(r'aria-disabled="true"', nav)) == len(NAV_KEYS) - 4


# --- Test case 3: mobile, dark theme, local Pico CSS ----------------------------------


def test_page_is_mobile_ready_and_dark(client: TestClient) -> None:
    html = client.get("/").text

    assert re.search(r'<meta name="viewport" content="width=device-width, initial-scale=1">', html)
    assert 'data-theme="dark"' in html


def test_pico_css_is_served_from_the_project_not_from_a_foreign_server(
    client: TestClient,
) -> None:
    html = client.get("/").text
    links = re.findall(r'<link\b[^>]*href="([^"]+)"', html)

    assert "/static/pico.min.css" in links
    assert all(link.startswith("/") and not link.startswith("//") for link in links)
    # Scripts only from the app itself (issue #39 added one), never from a foreign server.
    scripts = re.findall(r'<script\b[^>]*src="([^"]+)"', html)
    assert scripts == ["/static/app.js"]
    assert len(re.findall(r"<script\b", html)) == len(scripts)
    css = client.get("/static/pico.min.css")
    assert css.status_code == 200
    assert "Pico CSS" in css.text


# --- Test case 4: translations ---------------------------------------------------------


def test_default_language_is_german(client: TestClient) -> None:
    html = client.get("/").text

    assert '<html lang="de"' in html
    assert locale("de")["nav.articles"] in html


def test_no_visible_text_is_hardcoded_in_templates() -> None:
    texts = {value for name in ("de", "en") for value in locale(name).values()}
    for template in (APP_DIR / "templates").glob("*.html"):
        source = template.read_text(encoding="utf-8")
        for text in texts:
            assert text not in source, f"{template.name} contains hardcoded text: {text}"


# --- Test case 5: switching language ---------------------------------------------------


def test_english_can_be_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(monkeypatch, MAKERHUB_LANGUAGE="en") as client:
        html = client.get("/").text

    assert '<html lang="en"' in html
    assert locale("en")["nav.articles"] in html
    assert locale("de")["nav.articles"] not in html


@pytest.mark.parametrize("value", ["fr", "", "../de", "de-AT"])
def test_unknown_language_falls_back_to_german(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    with make_client(monkeypatch, MAKERHUB_LANGUAGE=value) as client:
        assert '<html lang="de"' in client.get("/").text


def test_language_value_ignores_case_and_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(monkeypatch, MAKERHUB_LANGUAGE=" EN ") as client:
        assert '<html lang="en"' in client.get("/").text


def test_german_and_english_have_the_same_keys() -> None:
    assert locale("de").keys() == locale("en").keys()
    assert all(value.strip() for name in ("de", "en") for value in locale(name).values())


# --- Test case 6: default = nobody may embed ------------------------------------------


def test_by_default_nobody_may_embed_the_page(client: TestClient) -> None:
    response = client.get("/")

    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["x-frame-options"] == "DENY"


# --- Test case 7: configured origins ---------------------------------------------------


def test_configured_origin_may_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(monkeypatch, MAKERHUB_EMBED_ORIGINS="https://bambuddy.example.com") as client:
        response = client.get("/")

    assert (
        response.headers["content-security-policy"]
        == "frame-ancestors 'self' https://bambuddy.example.com"
    )
    assert "x-frame-options" not in response.headers


def test_several_origins_separated_by_comma(monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(
        monkeypatch,
        MAKERHUB_EMBED_ORIGINS="https://bambuddy.example.com, http://localhost:8080",
    ) as client:
        response = client.get("/")

    assert (
        response.headers["content-security-policy"]
        == "frame-ancestors 'self' https://bambuddy.example.com http://localhost:8080"
    )


# --- Test case 8: invalid entries are rejected -----------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://*.example.com",
        "bambuddy.example.com",
        "ftp://example.com",
        "https://example.com/path",
        "https://example.com;",
        "https://exa mple.com",
        "https://example.com, *",
        "https://example.com,,https://example.org",
        "'none'",
        "https://",
        "https://example.com:٨٠",
        "https://example.com:99999",
        "https://a.example\nX-Injected: y",
        "HTTPS://example.com",
    ],
)
def test_invalid_embed_origins_stop_the_start(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAKERHUB_EMBED_ORIGINS", value)

    with pytest.raises(ConfigError) as error:
        create_app()

    assert "MAKERHUB_EMBED_ORIGINS" in str(error.value)


def test_empty_embed_origins_means_nobody() -> None:
    assert parse_embed_origins("") == ()
    assert parse_embed_origins("   ") == ()


def test_settings_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAKERHUB_LANGUAGE", raising=False)
    monkeypatch.delenv("MAKERHUB_EMBED_ORIGINS", raising=False)
    monkeypatch.delenv("MAKERHUB_DATA_DIR", raising=False)
    monkeypatch.delenv("MAKERHUB_INSECURE_COOKIES", raising=False)
    monkeypatch.delenv("MAKERHUB_TIMEZONE", raising=False)

    assert Settings.from_env() == Settings(
        language="de", embed_origins=(), data_dir=Path("data"), insecure_cookies=False
    )


# --- Test case 9: headers on every response --------------------------------------------


@pytest.mark.parametrize("path", ["/", "/healthz", "/does-not-exist", "/static/pico.min.css"])
def test_embedding_headers_apply_to_all_responses(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    with make_client(monkeypatch, MAKERHUB_EMBED_ORIGINS="https://bambuddy.example.com") as client:
        response = client.get(path)

    assert (
        "frame-ancestors 'self' https://bambuddy.example.com"
        in (response.headers["content-security-policy"])
    )


@pytest.mark.parametrize("path", ["/healthz", "/does-not-exist", "/static/pico.min.css"])
def test_default_headers_apply_to_all_responses(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["x-frame-options"] == "DENY"


def test_embedding_headers_also_apply_to_unhandled_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAKERHUB_EMBED_ORIGINS", raising=False)
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    with signed_in_client(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["x-frame-options"] == "DENY"
    assert "secret" not in response.text
