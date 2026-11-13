"""Two-factor login with app codes and recovery codes (issue #30, test cases 1-19).

Test case 20 (phone view) is in tests/browser/test_two_factor.py. All keys, names and
passwords here are invented.
"""

import json
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app import auth, cli, two_factor
from app.config import ConfigError, Settings, parse_two_factor_key
from app.db import make_engine
from app.main import create_app
from app.models import ChangeLogEntry, LoginLogEntry, LoginSession, RecoveryCode, User
from app.two_factor import Cipher
from tests.auth import (
    ADMIN_NAME,
    ADMIN_PASSWORD,
    EMPLOYEE_NAME,
    OTHER_PASSWORD,
    AppClient,
    add_user,
    client_as,
    csrf_from,
    log_in,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
REPO_DIR = APP_DIR.parent
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
TEST_KEY = "erfundener-test-schluessel-0123456789abcdef"
OTHER_KEY = "ein-anderer-erfundener-schluessel-fedcba9876543210"


@pytest.fixture(autouse=True)
def two_factor_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_2FA_KEY", TEST_KEY)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """Lets a test move the app's clock; app codes follow the same clock."""
    state = {"now": auth.utcnow()}
    monkeypatch.setattr(auth, "utcnow", lambda: state["now"])

    def advance(delta: timedelta) -> None:
        state["now"] = state["now"] + delta

    return advance


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


def user_named(username: str = ADMIN_NAME) -> User:
    with db() as session:
        return session.query(User).filter_by(username=username).one()


def code_now(secret: str, steps: int = 0) -> str:
    return two_factor.code_for_step(secret, two_factor.time_step(auth.utcnow()) + steps)


def secret_on_page(html: str) -> str:
    return re.search(r'<code class="secret">([A-Z2-7 ]+)</code>', html).group(1).replace(" ", "")


def codes_on_page(html: str) -> list[str]:
    return re.findall(r"<li><code>([0-9a-f]{4}(?:-[0-9a-f]{4}){3})</code></li>", html)


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


def enable(client: AppClient, clock=None) -> tuple[str, list[str]]:
    """Turns two-factor login on for the logged-in user; returns secret and recovery codes.

    With a clock, it moves on by some minutes, so that codes around the current one are
    not refused for being older than the code used for the setup.
    """
    start = client.post("/account/2fa/start", data={"tfa_password": ADMIN_PASSWORD})
    assert start.status_code == 200, start.text
    secret = secret_on_page(start.text)
    done = client.post("/account/2fa/confirm", data={"code": code_now(secret)})
    assert done.status_code == 200, done.text
    if clock is not None:
        clock(timedelta(minutes=3))
    return secret, codes_on_page(done.text)


def password_step(visitor: AppClient, username=ADMIN_NAME, password=ADMIN_PASSWORD, **kw):
    return log_in(visitor, username, password, follow_redirects=False, **kw)


def code_step(visitor: AppClient, code: str, page_url: str = "/login/code"):
    """Sends the code on the code page; returns the answer (not followed)."""
    page = visitor.get(page_url, follow_redirects=False)
    assert page.status_code == 200, page.headers.get("location")
    next_url = re.search(r'name="next" value="([^"]*)"', page.text).group(1)
    form = {"csrf_token": csrf_from(page.text), "code": code, "next": next_url}
    return visitor.post("/login/code", data=form, follow_redirects=False)


def full_login(app, code: str, username=ADMIN_NAME, password=ADMIN_PASSWORD) -> int:
    """A complete two-factor login in a fresh browser; returns the status of the code step."""
    with AppClient(app) as visitor:
        first = password_step(visitor, username, password)
        assert first.headers["location"].startswith("/login/code")
        return code_step(visitor, code).status_code


def is_logged_in(visitor: AppClient) -> bool:
    return visitor.get("/", follow_redirects=False).status_code == 200


def changes() -> list[ChangeLogEntry]:
    with db() as session:
        return list(session.query(ChangeLogEntry).order_by(ChangeLogEntry.id))


def login_events() -> list[str]:
    with db() as session:
        return [e.event for e in session.query(LoginLogEntry).order_by(LoginLogEntry.id)]


def database_text(data_dir: Path) -> str:
    return (data_dir / "makerhub3d.db").read_bytes().decode("utf-8", errors="ignore")


@pytest.fixture
def two_factor_admin(client: AppClient, clock) -> Iterator[tuple[AppClient, str, list[str]]]:
    """Logged-in admin with two-factor login switched on."""
    secret, codes = enable(client, clock)
    yield client, secret, codes


# --- Test cases 1-3: setting up --------------------------------------------------------


def test_without_two_factor_the_password_is_enough(client: AppClient) -> None:
    with AppClient(client.app) as visitor:
        response = password_step(visitor)

    assert response.headers["location"] == "/"


def test_my_account_offers_the_setup(client: AppClient) -> None:
    page = client.get("/account")

    assert DE["two_factor.title"] in page.text
    assert DE["two_factor.status.off"] in page.text
    assert 'action="/account/2fa/start"' in page.text


def test_setup_needs_the_current_password(client: AppClient) -> None:
    response = client.post("/account/2fa/start", data={"tfa_password": "falsches-passwort"})

    assert response.status_code == 422
    assert field_error(response.text, "tfa_password") == DE["account.error.current_wrong"]
    assert user_named().totp_pending_secret is None
    assert client.get("/account/2fa/setup", follow_redirects=False).status_code == 303


def test_setup_shows_qr_code_and_secret_as_text(client: AppClient) -> None:
    page = client.post("/account/2fa/start", data={"tfa_password": ADMIN_PASSWORD})

    assert re.search(r'<img src="data:image/svg\+xml[^"]+" alt="[^"]+">', page.text)
    assert len(secret_on_page(page.text)) == 32
    # Reloading shows the same secret; nothing secret is kept in the browser's cache.
    again = client.get("/account/2fa/setup")
    assert secret_on_page(again.text) == secret_on_page(page.text)
    assert again.headers["cache-control"] == "no-store"


def test_qr_code_holds_the_secret_for_the_app(client: AppClient) -> None:
    uri = two_factor.provisioning_uri("JBSWY3DPEHPK3PXP", ADMIN_NAME)

    assert uri.startswith("otpauth://totp/MakerHub%203D:werkstatt?")
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "issuer=MakerHub%203D" in uri


def test_two_factor_is_off_until_the_first_code(client: AppClient) -> None:
    page = client.post("/account/2fa/start", data={"tfa_password": ADMIN_PASSWORD})
    secret = secret_on_page(page.text)

    wrong = client.post("/account/2fa/confirm", data={"code": code_now(secret, steps=3)})

    assert wrong.status_code == 422
    assert DE["two_factor.setup.failed"] in wrong.text
    assert user_named().totp_secret is None
    assert DE["two_factor.status.off"] in client.get("/account").text
    with AppClient(client.app) as visitor:
        assert password_step(visitor).headers["location"] == "/"


def test_first_code_switches_it_on(client: AppClient) -> None:
    page = client.post("/account/2fa/start", data={"tfa_password": ADMIN_PASSWORD})

    done = client.post("/account/2fa/confirm", data={"code": code_now(secret_on_page(page.text))})

    assert done.status_code == 200
    assert user_named().totp_secret is not None
    assert user_named().totp_pending_secret is None
    assert DE["two_factor.status.on"] in client.get("/account").text
    # The setup page is gone once it is on.
    assert client.get("/account/2fa/setup", follow_redirects=False).status_code == 303


# --- Test cases 4 + 5: recovery codes and storage --------------------------------------


def test_ten_recovery_codes_are_shown_only_once(client: AppClient) -> None:
    page = client.post("/account/2fa/start", data={"tfa_password": ADMIN_PASSWORD})
    done = client.post("/account/2fa/confirm", data={"code": code_now(secret_on_page(page.text))})

    codes = codes_on_page(done.text)
    assert len(codes) == len(set(codes)) == 10
    assert done.headers["cache-control"] == "no-store"
    account = client.get("/account").text
    assert not any(code in account for code in codes)
    assert DE["two_factor.codes_left"].format(count=10) in account


def test_secret_and_codes_are_not_stored_in_plain_text(
    two_factor_admin: tuple[AppClient, str, list[str]], data_dir: Path
) -> None:
    _, secret, codes = two_factor_admin
    stored = user_named().totp_secret

    content = database_text(data_dir)
    assert secret not in content
    assert not any(code in content or code.replace("-", "") in content for code in codes)
    assert Cipher(TEST_KEY).decrypt(stored) == secret
    assert Cipher(OTHER_KEY).decrypt(stored) is None
    with db() as session:
        hashes = {row.code_hash for row in session.query(RecoveryCode)}
    assert hashes == {two_factor.recovery_code_hash(code) for code in codes}


# --- Test case 6: turning it off -------------------------------------------------------


def test_turning_off_needs_password_and_code(
    two_factor_admin: tuple[AppClient, str, list[str]],
) -> None:
    client, secret, _ = two_factor_admin
    wrong_password = client.post(
        "/account/2fa/disable", data={"off_password": "falsch-falsch", "off_code": code_now(secret)}
    )
    wrong_code = client.post(
        "/account/2fa/disable", data={"off_password": ADMIN_PASSWORD, "off_code": "000000"}
    )

    assert wrong_password.status_code == wrong_code.status_code == 422
    assert field_error(wrong_password.text, "off_password") == DE["account.error.current_wrong"]
    assert field_error(wrong_code.text, "off_code") == DE["account.error.code_wrong"]
    assert user_named().totp_secret is not None


def test_turned_off_the_password_is_enough_again(
    two_factor_admin: tuple[AppClient, str, list[str]],
) -> None:
    client, secret, _ = two_factor_admin

    response = client.post(
        "/account/2fa/disable", data={"off_password": ADMIN_PASSWORD, "off_code": code_now(secret)}
    )

    assert response.status_code == 200
    assert DE["two_factor.disabled"] in response.text
    assert user_named().totp_secret is None
    with db() as session:
        assert session.query(RecoveryCode).count() == 0
    with AppClient(client.app) as visitor:
        assert password_step(visitor).headers["location"] == "/"


def test_turning_off_works_with_a_recovery_code(
    two_factor_admin: tuple[AppClient, str, list[str]],
) -> None:
    client, _, codes = two_factor_admin

    client.post("/account/2fa/disable", data={"off_password": ADMIN_PASSWORD, "off_code": codes[0]})

    assert user_named().totp_secret is None


# --- Test case 7: new recovery codes ---------------------------------------------------


def test_new_recovery_codes_replace_the_old_ones(
    two_factor_admin: tuple[AppClient, str, list[str]],
) -> None:
    client, secret, old_codes = two_factor_admin
    wrong = client.post("/account/2fa/codes", data={"renew_code": "123456"})
    assert wrong.status_code == 422
    assert field_error(wrong.text, "renew_code") == DE["account.error.code_wrong"]

    page = client.post("/account/2fa/codes", data={"renew_code": code_now(secret)})

    new_codes = codes_on_page(page.text)
    assert len(new_codes) == 10
    assert not set(new_codes) & set(old_codes)
    assert full_login(client.app, old_codes[0]) == 401
    assert full_login(client.app, new_codes[0]) == 303


# --- Test case 8: no key ----------------------------------------------------------------


def test_without_key_setup_is_not_possible(monkeypatch: pytest.MonkeyPatch, client) -> None:
    monkeypatch.delenv("MAKERHUB_2FA_KEY")
    with AppClient(create_app()) as other:
        response = log_in(other)
        assert response.status_code == 200
        other.csrf_token = csrf_from(other.get("/").text)

        page = other.get("/account")
        start = other.post(
            "/account/2fa/start", data={"tfa_password": ADMIN_PASSWORD}, follow_redirects=False
        )

    assert DE["two_factor.no_key"] in page.text
    assert 'action="/account/2fa/start"' not in page.text
    assert start.headers["location"] == "/account"
    assert user_named().totp_pending_secret is None


# --- Test cases 9-15: logging in -------------------------------------------------------


def test_password_alone_does_not_log_in(two_factor_admin) -> None:
    client, _, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        first = password_step(visitor)

        assert first.status_code == 303
        assert first.headers["location"] == "/login/code"
        assert not is_logged_in(visitor)
        page = visitor.get("/login/code")
        assert DE["login_code.title"] in page.text
        # Other pages still lead to the login.
        assert (
            visitor.get("/settings", follow_redirects=False)
            .headers["location"]
            .startswith("/login")
        )


def test_right_code_logs_in(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        password_step(visitor)

        response = code_step(visitor, code_now(secret))

        assert response.headers["location"] == "/"
        assert is_logged_in(visitor)


def test_wrong_code_does_not_log_in(two_factor_admin) -> None:
    client, _, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        password_step(visitor)

        response = code_step(visitor, "000000")

        assert response.status_code == 401
        assert DE["login_code.failed"] in response.text
        assert not is_logged_in(visitor)
        # The code page stays usable for another try.
        assert visitor.get("/login/code", follow_redirects=False).status_code == 200


@pytest.mark.parametrize(("steps", "accepted"), [(-2, False), (-1, True), (1, True), (2, False)])
def test_one_step_before_or_after_is_accepted(two_factor_admin, steps, accepted) -> None:
    client, secret, _ = two_factor_admin

    status = full_login(client.app, code_now(secret, steps))

    assert status == (303 if accepted else 401)


def test_a_code_works_only_once(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin
    code = code_now(secret)

    assert full_login(client.app, code) == 303
    assert full_login(client.app, code) == 401


def test_an_older_code_after_a_newer_one_is_refused(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin

    assert full_login(client.app, code_now(secret, 1)) == 303
    assert full_login(client.app, code_now(secret)) == 401


def test_spaces_in_the_code_do_not_matter(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin
    code = code_now(secret)

    assert full_login(client.app, f" {code[:3]} {code[3:]} ") == 303


def test_recovery_code_works_exactly_once(two_factor_admin) -> None:
    client, _, codes = two_factor_admin

    assert full_login(client.app, codes[3].upper().replace("-", " ")) == 303
    assert full_login(client.app, codes[3]) == 401
    assert DE["two_factor.codes_left"].format(count=9) in client.get("/account").text


def test_recovery_codes_of_another_user_do_not_work(two_factor_admin) -> None:
    client, _, codes = two_factor_admin
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        employee.post("/account/2fa/start", data={"tfa_password": OTHER_PASSWORD})
        secret = secret_on_page(employee.get("/account/2fa/setup").text)
        employee.post("/account/2fa/confirm", data={"code": code_now(secret)})

    assert full_login(client.app, codes[0], EMPLOYEE_NAME, OTHER_PASSWORD) == 401


def test_five_wrong_codes_lock_the_account(two_factor_admin, clock) -> None:
    client, secret, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        password_step(visitor)
        statuses = [code_step(visitor, "000000").status_code for _ in range(4)]
        assert statuses == [401] * 4

        fifth = visitor.post(
            "/login/code",
            data={"csrf_token": csrf_from(visitor.get("/login").text), "code": "000000"},
            follow_redirects=False,
        )

        assert fifth.headers["location"] == "/login"
    assert user_named().locked_until is not None
    # Even the right password and code do not help during the lock ...
    with AppClient(client.app) as visitor:
        assert password_step(visitor).status_code == 401
    # ... but afterwards they do.
    clock(timedelta(minutes=15, seconds=1))
    assert full_login(client.app, code_now(secret)) == 303


def test_right_password_does_not_reset_the_count_of_wrong_codes(two_factor_admin) -> None:
    client, _, _ = two_factor_admin
    for _ in range(4):
        with AppClient(client.app) as visitor:
            password_step(visitor)
            assert code_step(visitor, "000000").status_code == 401

    with AppClient(client.app) as visitor:
        password_step(visitor)
        code_step(visitor, "000000")

    assert user_named().locked_until is not None


def test_code_step_ends_after_5_minutes(two_factor_admin, clock) -> None:
    client, secret, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        password_step(visitor)
        clock(timedelta(minutes=5, seconds=1))

        page = visitor.get("/login/code", follow_redirects=False)
        token = csrf_from(visitor.get("/login").text)
        post = visitor.post(
            "/login/code",
            data={"csrf_token": token, "code": code_now(secret)},
            follow_redirects=False,
        )

        assert page.headers["location"] == "/login"
        assert post.headers["location"] == "/login"
        assert not is_logged_in(visitor)


def test_code_step_needs_the_password_first(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        page = visitor.get("/login/code", follow_redirects=False)
        token = csrf_from(visitor.get("/login").text)
        forged = {"csrf_token": token, "code": code_now(secret)}
        visitor.cookies.set("__Host-makerhub_2fa", "ausgedacht", domain="testserver")

        response = visitor.post("/login/code", data=forged, follow_redirects=False)

        assert page.headers["location"] == "/login"
        assert response.headers["location"] == "/login"
        assert not is_logged_in(visitor)


def test_pending_login_is_stored_only_as_fingerprint(two_factor_admin, data_dir) -> None:
    client, _, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        password_step(visitor)
        token = visitor.cookies.get("__Host-makerhub_2fa")

    assert token
    assert token not in database_text(data_dir)


def test_pending_cookie_is_protected(two_factor_admin) -> None:
    client, _, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        header = password_step(visitor).headers["set-cookie"]

    assert "__Host-makerhub_2fa=" in header
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag in header


def test_requested_page_opens_after_the_code(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        first = password_step(visitor, next_url="/settings?saved=1")
        assert first.headers["location"] == "/login/code?next=%2Fsettings%3Fsaved%3D1"

        response = code_step(visitor, code_now(secret), first.headers["location"])

        assert response.headers["location"] == "/settings?saved=1"


def test_after_the_code_never_to_a_foreign_address(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        first = password_step(visitor, next_url="//example.com/")
        response = code_step(visitor, code_now(secret), first.headers["location"])

        assert response.headers["location"] == "/"


def test_deactivated_user_cannot_finish_the_code_step(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        password_step(visitor)
        with db() as session, session.begin():
            session.query(User).filter_by(username=ADMIN_NAME).one().active = False

        assert code_step(visitor, code_now(secret)).headers["location"] == "/login"


# --- Test cases 16 + 17: the key -------------------------------------------------------


def test_missing_key_without_two_factor_users_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAKERHUB_2FA_KEY")
    with AppClient(create_app()) as visitor:
        assert visitor.get("/healthz").status_code == 200


@pytest.mark.parametrize("value", ["zu-kurz", "a" * 31, "a" * 20 + " " + "b" * 20, "ä" * 40])
def test_invalid_key_stops_the_start(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAKERHUB_2FA_KEY", value)

    with pytest.raises(ConfigError) as error:
        create_app()
    assert value not in str(error.value)


def test_key_is_not_shown_in_settings_repr() -> None:
    assert TEST_KEY not in repr(Settings.from_env())
    assert parse_two_factor_key("  " + TEST_KEY + "\n") == TEST_KEY
    assert parse_two_factor_key("") is None


@pytest.mark.parametrize("new_key", [OTHER_KEY, None])
def test_wrong_or_missing_key_stops_the_start_with_both_ways_out(
    two_factor_admin, monkeypatch, capfd, new_key
) -> None:
    if new_key is None:
        monkeypatch.delenv("MAKERHUB_2FA_KEY")
    else:
        monkeypatch.setenv("MAKERHUB_2FA_KEY", new_key)
    capfd.readouterr()

    with pytest.raises(RuntimeError), AppClient(create_app()):
        pass

    message = capfd.readouterr().err
    assert DE["two_factor.key_mismatch"] in message
    assert "python -m app.cli reset-2fa" in message
    for secret_value in (TEST_KEY, OTHER_KEY, two_factor_admin[1]):
        assert secret_value not in message


def test_reset_command_turns_two_factor_off_for_everybody(
    two_factor_admin, monkeypatch, capsys
) -> None:
    client, _, _ = two_factor_admin
    client.post("/settings/users/new", data={
        "username": EMPLOYEE_NAME, "role": "employee",
        "password": OTHER_PASSWORD, "password_repeat": OTHER_PASSWORD,
    })  # fmt: skip
    with db() as session:
        users_before = [(u.username, u.role, u.password_hash) for u in session.query(User)]
    monkeypatch.setenv("MAKERHUB_2FA_KEY", OTHER_KEY)

    assert cli.main(["reset-2fa"]) == 0

    assert DE["cli.reset_2fa.done"].format(count=1) in capsys.readouterr().out
    with db() as session:
        users_after = [(u.username, u.role, u.password_hash) for u in session.query(User)]
        assert session.query(RecoveryCode).count() == 0
        assert session.query(User).filter(User.totp_secret.is_not(None)).count() == 0
    assert users_after == users_before
    entry = changes()[-1]
    assert (entry.action, entry.user_id) == ("system.2fa_reset_all", None)
    # The app starts with the new key, and the password alone logs in again.
    with AppClient(create_app()) as visitor:
        assert password_step(visitor).headers["location"] == "/"


def test_reset_command_shows_usage_for_unknown_commands(capsys) -> None:
    assert cli.main([]) == 2
    assert cli.main(["alles-loeschen"]) == 2
    assert DE["cli.usage"] in capsys.readouterr().err


def test_key_is_explained_in_env_example_and_guide() -> None:
    example = (REPO_DIR / ".env.example").read_text(encoding="utf-8")
    guide = (REPO_DIR / "docs" / "erster-start.md").read_text(encoding="utf-8")

    assert "# MAKERHUB_2FA_KEY=" in example
    assert "openssl rand -hex 32" in example
    for text in ("MAKERHUB_2FA_KEY", "openssl rand -hex 32", "reset-2fa", "Uhr"):
        assert text in guide, text


# --- Test case 18: logs -----------------------------------------------------------------


def test_switching_on_off_and_new_codes_are_logged(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin
    client.post("/account/2fa/codes", data={"renew_code": code_now(secret)})
    client.post(
        "/account/2fa/disable",
        data={"off_password": ADMIN_PASSWORD, "off_code": code_now(secret, 1)},
    )

    actions = [(e.action, e.subject) for e in changes()][-3:]
    assert actions == [
        ("account.2fa_enabled", ADMIN_NAME),
        ("account.2fa_codes_renewed", ADMIN_NAME),
        ("account.2fa_disabled", ADMIN_NAME),
    ]
    page = client.get("/settings/log").text
    for key in ("2fa_enabled", "2fa_codes_renewed", "2fa_disabled"):
        assert DE[f"log.action.account.{key}"] in page


def test_wrong_codes_and_recovery_logins_are_logged(two_factor_admin) -> None:
    client, secret, codes = two_factor_admin
    before = len(login_events())

    with AppClient(client.app) as visitor:
        password_step(visitor)
        code_step(visitor, "000000")
        code_step(visitor, code_now(secret))
    full_login(client.app, codes[0])

    assert login_events()[before:] == ["wrong_code", "success", "recovery_code_used"]
    page = client.get("/settings/log/logins").text
    assert DE["log.event.wrong_code"] in page
    assert DE["log.event.recovery_code_used"] in page


def test_server_command_is_shown_as_such_in_the_log(two_factor_admin) -> None:
    client, _, _ = two_factor_admin
    cli.main(["reset-2fa"])

    page = client.get("/settings/log").text

    assert DE["log.action.system.2fa_reset_all"] in page
    assert DE["log.server_command"] in page


def test_no_secret_or_code_in_any_log(two_factor_admin) -> None:
    client, secret, codes = two_factor_admin
    full_login(client.app, codes[1])
    full_login(client.app, "999999")

    with db() as session:
        logged = " ".join(
            f"{e.action} {e.subject} {e.details}" for e in session.query(ChangeLogEntry)
        )
    assert secret not in logged
    assert not any(code in logged for code in codes)
    assert "999999" not in logged


def test_wrong_password_or_code_in_my_account_counts_as_failure(two_factor_admin) -> None:
    client, _, _ = two_factor_admin
    for _ in range(4):
        client.post("/account/2fa/codes", data={"renew_code": "000000"})

    response = client.post(
        "/account/2fa/codes", data={"renew_code": "000000"}, follow_redirects=False
    )

    assert response.headers["location"] == "/login"
    assert user_named().locked_until is not None
    with db() as session:
        assert session.query(LoginSession).count() == 0
    assert login_events()[-2:] == ["wrong_code", "locked"]


# --- Test case 19: form tokens ----------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/account/2fa/start", "/account/2fa/confirm", "/account/2fa/disable",
             "/account/2fa/codes"],
)  # fmt: skip
def test_new_account_forms_need_the_form_token(client: AppClient, path: str) -> None:
    response = client.post(path, data={"csrf_token": "falsch", "tfa_password": ADMIN_PASSWORD})

    assert response.status_code == 403
    assert user_named().totp_pending_secret is None


def test_new_forms_carry_the_form_token(two_factor_admin) -> None:
    client, _, _ = two_factor_admin
    account = client.get("/account").text
    with AppClient(client.app) as visitor:
        password_step(visitor)
        code_page = visitor.get("/login/code").text

    assert account.count('name="csrf_token"') >= 4  # password, renew, off, logout
    assert csrf_from(code_page)


def test_code_step_without_form_token_is_rejected(two_factor_admin) -> None:
    client, secret, _ = two_factor_admin
    with AppClient(client.app) as visitor:
        password_step(visitor)

        response = visitor.post("/login/code", data={"code": code_now(secret)})

        assert response.status_code == 403
        assert not is_logged_in(visitor)


def test_code_page_in_english(two_factor_admin, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_LANGUAGE", "en")
    with AppClient(create_app()) as visitor:
        password_step(visitor)
        page = visitor.get("/login/code")

    assert "Enter code" in page.text
