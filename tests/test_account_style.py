"""Account, two-factor, login and setup pages in the new style (issue #92, part 6c).

The test case numbers refer to the comment on the issue. The phone view and the light/dark
theme are checked in tests/browser/test_account_style.py. All data is invented.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.auth import ADMIN_NAME, ADMIN_PASSWORD, AppClient, log_in, signed_in_client
from tests.test_account import change_form
from tests.test_two_factor import TEST_KEY, enable

DE = json.loads(
    (Path(__file__).resolve().parent.parent / "app" / "locales" / "de.json").read_text(
        encoding="utf-8"
    )
)
CARD = 'class="detail-card"'
AUTH_CARD = '<article class="auth-box detail-card">'


@pytest.fixture(autouse=True)
def two_factor_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_2FA_KEY", TEST_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


# --- Case 1: my account is made of cards ----------------------------------------------------


def test_account_shows_password_and_two_factor_as_cards(client: AppClient) -> None:
    html = client.get("/account").text

    assert html.count(CARD) == 2
    cards = re.findall(r'<article class="detail-card">(.*?)</article>', html, re.DOTALL)
    assert DE["account.password.title"] in cards[0]
    assert 'action="/account/password"' in cards[0]
    assert DE["two_factor.title"] in cards[1]
    assert 'action="/account/2fa/start"' in cards[1]


# --- Case 2: changing the password still works ------------------------------------------------


def test_password_change_still_works_and_shows_messages(client: AppClient) -> None:
    bad = client.post("/account/password", data=change_form(repeat="anders"))
    assert bad.status_code == 422
    assert DE["account.has_errors"] in bad.text
    assert bad.text.count(CARD) == 2

    good = client.post("/account/password", data=change_form(), follow_redirects=True)
    assert DE["account.changed"] in good.text


# --- Case 3: two-factor on: status, renew and switch off sit in the card ----------------------


def test_account_with_two_factor_on_keeps_all_functions_in_its_card(client: AppClient) -> None:
    enable(client)
    html = client.get("/account").text

    card = re.findall(r'<article class="detail-card">(.*?)</article>', html, re.DOTALL)[1]
    assert DE["two_factor.status.on"] in card
    assert 'action="/account/2fa/codes"' in card
    assert 'action="/account/2fa/disable"' in card


# --- Cases 4 and 5: setup page and recovery codes are cards ------------------------------------


def test_two_factor_setup_page_is_a_card(client: AppClient) -> None:
    html = client.post("/account/2fa/start", data={"tfa_password": ADMIN_PASSWORD}).text

    assert CARD in html
    assert 'class="qr-code"' in html
    assert 'action="/account/2fa/confirm"' in html
    assert 'href="/account"' in html


def test_recovery_codes_page_is_a_card(client: AppClient) -> None:
    _, codes = enable(client)
    # The page is only shown once, right after confirming; enable() returned its codes.
    assert len(codes) > 0
    start = client.post("/account/2fa/codes", data={"renew_code": codes[0]})
    assert start.status_code == 200
    html = start.text

    assert CARD in html
    assert 'class="recovery-codes"' in html
    assert DE["two_factor.codes.warning"] in html
    assert 'href="/account"' in html


# --- Case 6: login, code entry and first setup are cards --------------------------------------


def test_setup_page_is_a_centered_card(anon_client: TestClient) -> None:
    html = anon_client.get("/setup").text

    assert AUTH_CARD in html
    assert 'action="/setup"' in html


def test_login_page_is_a_centered_card(client: AppClient) -> None:
    with AppClient(client.app) as visitor:
        html = visitor.get("/login").text

    assert AUTH_CARD in html
    assert 'action="/login"' in html


def test_code_page_is_a_centered_card(client: AppClient) -> None:
    enable(client)
    with AppClient(client.app) as visitor:
        first = log_in(visitor, ADMIN_NAME, ADMIN_PASSWORD, follow_redirects=False)
        assert first.headers["location"].startswith("/login/code")
        html = visitor.get("/login/code").text

    assert AUTH_CARD in html
    assert 'action="/login/code"' in html
