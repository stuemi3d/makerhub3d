"""Status of the Bambuddy connection (issue #122, test cases 1-5)."""

import json
import re
from pathlib import Path

import httpx2
import pytest

from tests.auth import EMPLOYEE_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_settings import KEY, TEST_SECRET_KEY, URL, answer_with, good_handler, save

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


def status_block(html: str) -> str:
    match = re.search(r'<section class="bambuddy-status[^>]*>.*?</section>', html, re.S)
    assert match, "status block not found"
    return match.group(0)


def tab_status(html: str) -> str:
    match = re.search(r'<a href="/settings/bambuddy"[^>]*>(.*?)</a>', html, re.S)
    assert match, "tab not found"
    return match.group(1)


def test_set_up_shows_green_status_with_the_address(client: AppClient) -> None:
    save(client)
    block = status_block(client.get("/settings/bambuddy").text)
    assert DE["bambuddy.status.ready"] in block
    assert "chip-active" in block
    assert URL in block
    assert KEY not in block


def test_nothing_saved_shows_grey_status_and_what_is_missing(client: AppClient) -> None:
    block = status_block(client.get("/settings/bambuddy").text)
    assert DE["bambuddy.status.not_ready"] in block
    assert "chip-inactive" in block
    assert DE["bambuddy.status.missing_both"] in block


def test_missing_key_or_address_is_named(client: AppClient) -> None:
    save(client, api_key="")
    assert DE["bambuddy.status.missing_key"] in status_block(client.get("/settings/bambuddy").text)


def test_missing_url_alone_is_named() -> None:
    from app.bambuddy_pages import missing_parts

    assert missing_parts("", True) == "bambuddy.status.missing_url"
    assert missing_parts(URL, True) is None


def test_test_result_shows_in_the_status_block(client: AppClient) -> None:
    save(client)
    answer_with(client, good_handler)
    ok = status_block(client.post("/settings/bambuddy/test", data={}).text)
    assert DE["bambuddy.test.ok"] in ok
    answer_with(client, lambda request: httpx2.Response(401, json={}))
    bad = status_block(client.post("/settings/bambuddy/test", data={}).text)
    assert DE["bambuddy.error.rejected"] in bad
    assert "chip-warn" in bad


def test_opening_the_page_does_not_call_bambuddy(client: AppClient) -> None:
    save(client)
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return good_handler(request)

    answer_with(client, handler)
    client.get("/settings/bambuddy")
    client.get("/settings")
    assert calls == []


def test_tab_shows_a_dot_for_the_state(client: AppClient) -> None:
    before = tab_status(client.get("/settings").text)
    assert "status-dot-off" in before and DE["bambuddy.status.not_ready"] in before
    save(client)
    after = tab_status(client.get("/settings").text)
    assert "status-dot-on" in after and DE["bambuddy.status.ready"] in after


def test_others_do_not_see_the_status_or_the_tab(client: AppClient) -> None:
    save(client)
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as other:
        page = other.get("/settings/appearance").text
    assert "/settings/bambuddy" not in page and "status-dot" not in page
    assert URL not in page


def test_texts_exist_in_both_languages() -> None:
    for key in (
        "bambuddy.status.ready",
        "bambuddy.status.not_ready",
        "bambuddy.status.missing_url",
        "bambuddy.status.missing_key",
        "bambuddy.status.missing_both",
    ):
        assert DE[key] and EN[key]
