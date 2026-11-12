"""First admin, login, logout, lockout, sessions and form tokens (issue #8, test cases 1-16)."""

import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import auth
from app.config import DATABASE_FILE, ConfigError, Settings
from app.db import make_engine, run_migrations
from app.main import create_app
from app.models import AppSettings, User, UserRole
from tests.auth import (
    ADMIN_NAME,
    ADMIN_PASSWORD,
    AppClient,
    csrf_from,
    log_in,
    setup_form,
    sign_in,
    signed_in_client,
)
from tests.forms import valid_form

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
PUBLIC_PATHS = {"/login", "/setup", "/healthz"}


def users() -> list[User]:
    with Session(make_engine(Settings.from_env().database_path)) as session:
        return list(session.query(User))


def stored_markup() -> Decimal:
    with Session(make_engine(Settings.from_env().database_path)) as session:
        return session.get(AppSettings, 1).markup


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


def is_login_page(response) -> bool:
    return response.status_code == 200 and 'action="/login"' in response.text


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """Lets a test move the app's clock forward."""
    start = auth.utcnow()
    state = {"now": start}
    monkeypatch.setattr(auth, "utcnow", lambda: state["now"])

    def advance(delta: timedelta) -> None:
        state["now"] = state["now"] + delta

    return advance


# --- Test case 1: without any user, every page leads to the setup page -------------------


@pytest.mark.parametrize("path", ["/", "/settings", "/login", "/does-not-exist"])
def test_without_user_every_page_leads_to_setup(anon_client: TestClient, path: str) -> None:
    response = anon_client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_setup_page_shows_the_form(anon_client: TestClient) -> None:
    response = anon_client.get("/setup")

    assert response.status_code == 200
    assert DE["setup.title"] in response.text
    for name in ("setup_code", "username", "password", "password_repeat"):
        assert f'name="{name}"' in response.text
    assert re.search(r'<input\b[^>]*type="password"[^>]*name="password"', response.text)


# --- Test case 2: wrong setup code ------------------------------------------------------


@pytest.mark.parametrize("code", ["", "falsch", "0000-0000-0000-0000"])
def test_wrong_setup_code_creates_nobody(anon_client: TestClient, code: str) -> None:
    response = anon_client.post("/setup", data=setup_form(anon_client, setup_code=code))

    assert response.status_code == 422
    assert field_error(response.text, "setup_code") == DE["setup.error.code"]
    assert users() == []


def test_setup_code_is_written_to_the_log_at_start(capfd: pytest.CaptureFixture) -> None:
    with AppClient(create_app()) as client:
        code = client.app.state.setup_code

    assert re.fullmatch(r"[0-9a-f]{4}(-[0-9a-f]{4}){3}", code)
    assert code in capfd.readouterr().err


def test_setup_code_changes_with_every_start() -> None:
    with AppClient(create_app()) as first:
        first_code = first.app.state.setup_code
    with AppClient(create_app()) as second:
        assert second.app.state.setup_code != first_code


def test_setup_code_ignores_case_spaces_and_dashes(anon_client: TestClient) -> None:
    code = anon_client.app.state.setup_code
    typed = " " + code.upper().replace("-", "") + " "

    response = anon_client.post("/setup", data=setup_form(anon_client, setup_code=typed))

    assert response.status_code == 200
    assert len(users()) == 1


# --- Test case 3: correct setup creates the admin and logs in --------------------------


def test_setup_creates_admin_and_logs_in(anon_client: TestClient) -> None:
    response = anon_client.post("/setup", data=setup_form(anon_client), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    [user] = users()
    assert user.username == ADMIN_NAME
    assert user.role == "admin"
    start_page = anon_client.get("/")
    assert start_page.status_code == 200
    assert DE["nav.logout"] in start_page.text


def test_username_is_trimmed(anon_client: TestClient) -> None:
    anon_client.post("/setup", data=setup_form(anon_client, username=f"  {ADMIN_NAME}  "))

    assert users()[0].username == ADMIN_NAME


def test_no_setup_code_is_printed_once_an_admin_exists(capfd: pytest.CaptureFixture) -> None:
    with signed_in_client():
        pass
    capfd.readouterr()

    with AppClient(create_app()) as client:
        assert client.app.state.setup_code is None

    assert DE["setup.log_code"].split("{code}")[0] not in capfd.readouterr().err


# --- Test case 4: invalid passwords and names ------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field", "message_key"),
    [
        ({"password": "elf-zeichen", "password_repeat": "elf-zeichen"}, "password",
         "user.error.password_too_short"),
        ({"password": "x" * 1025, "password_repeat": "x" * 1025}, "password",
         "user.error.password_too_long"),
        ({"password_repeat": ADMIN_PASSWORD + "!"}, "password_repeat",
         "user.error.password_mismatch"),
        ({"username": ""}, "username", "user.error.required"),
        ({"username": "   "}, "username", "user.error.required"),
        ({"username": "x" * 51}, "username", "user.error.username_too_long"),
    ],
)  # fmt: skip
def test_invalid_setup_input_creates_nobody(
    anon_client: TestClient, overrides: dict, field: str, message_key: str
) -> None:
    response = anon_client.post("/setup", data=setup_form(anon_client, **overrides))

    assert response.status_code == 422
    assert field_error(response.text, field) == DE[message_key]
    assert users() == []


def test_twelve_characters_are_enough(anon_client: TestClient) -> None:
    password = "zwölf-zeiche"
    assert len(password) == 12

    response = anon_client.post(
        "/setup", data=setup_form(anon_client, password=password, password_repeat=password)
    )

    assert response.status_code == 200
    assert len(users()) == 1


def test_passwords_are_never_shown_again_after_an_error(anon_client: TestClient) -> None:
    response = anon_client.post(
        "/setup", data=setup_form(anon_client, password_repeat="anderes-langes-passwort")
    )

    assert ADMIN_PASSWORD not in response.text
    assert "anderes-langes-passwort" not in response.text
    assert f'value="{ADMIN_NAME}"' in response.text


def test_setup_input_is_escaped(anon_client: TestClient) -> None:
    response = anon_client.post(
        "/setup", data=setup_form(anon_client, username='"><script>x</script>', password="kurz")
    )

    assert "<script>x</script>" not in response.text


# --- Test case 5: setup is closed once a user exists -----------------------------------


def test_setup_page_is_gone_once_a_user_exists(client: AppClient) -> None:
    assert client.get("/setup").status_code == 404


def test_setup_cannot_create_a_second_admin(anon_client: AppClient) -> None:
    code = anon_client.app.state.setup_code
    sign_in(anon_client)

    response = anon_client.post(
        "/setup", data=setup_form(anon_client, setup_code=code, username="zweiter-admin")
    )

    assert response.status_code == 404
    assert [user.username for user in users()] == [ADMIN_NAME]


def test_setup_is_gone_for_visitors_too() -> None:
    with signed_in_client():
        pass
    with AppClient(create_app()) as visitor:
        assert visitor.get("/setup").status_code == 404


# --- Test case 6: without login only the login page (and the agreed exceptions) --------


def app_page_paths() -> list[tuple[str, str]]:
    app = create_app()
    pages = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path not in PUBLIC_PATHS:
            pages.extend((method, route.path) for method in sorted(route.methods))
    return pages


def test_route_list_is_not_empty() -> None:
    assert ("GET", "/") in app_page_paths()
    assert ("POST", "/settings") in app_page_paths()


@pytest.fixture
def visitor() -> Iterator[AppClient]:
    """Someone who is not logged in, while an admin already exists."""
    with signed_in_client():
        pass
    with AppClient(create_app()) as test_client:
        yield test_client


@pytest.mark.parametrize(("method", "path"), app_page_paths())
def test_no_page_without_login(visitor: AppClient, method: str, path: str) -> None:
    response = visitor.request(method, path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_unknown_pages_also_lead_to_login(visitor: AppClient) -> None:
    response = visitor.get("/does-not-exist", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_settings_cannot_be_saved_without_login(visitor: AppClient) -> None:
    form = valid_form(markup="99", csrf_token=csrf_from(visitor.get("/login").text))

    visitor.post("/settings", data=form, follow_redirects=False)

    assert stored_markup() == Decimal("30")


def test_login_page_is_reachable(visitor: AppClient) -> None:
    response = visitor.get("/login")

    assert is_login_page(response)
    assert DE["login.title"] in response.text
    # Nothing but the login form: no navigation into the app.
    assert 'href="/settings"' not in response.text


@pytest.mark.parametrize("path", ["/healthz", "/static/pico.min.css", "/static/app.css"])
def test_agreed_exceptions_work_without_login(visitor: AppClient, path: str) -> None:
    assert visitor.get(path).status_code == 200


def test_after_login_the_requested_page_opens(visitor: AppClient) -> None:
    redirect = visitor.get("/settings?saved=1", follow_redirects=False)
    assert redirect.headers["location"] == "/login?next=%2Fsettings%3Fsaved%3D1"
    login_page = visitor.get(redirect.headers["location"])

    form = {
        "csrf_token": csrf_from(login_page.text),
        "username": ADMIN_NAME,
        "password": ADMIN_PASSWORD,
        "next": re.search(r'name="next" value="([^"]*)"', login_page.text).group(1),
    }
    response = visitor.post("/login", data=form, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=1"


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "/\r\nSet-Cookie: x=y",
        "/\tsettings",
        "settings",
        "",
    ],
)
def test_after_login_never_to_a_foreign_address(visitor: AppClient, next_url: str) -> None:
    response = log_in(visitor, next_url=next_url, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


# --- Test case 7: correct name and password --------------------------------------------


def test_correct_login_opens_the_app(visitor: AppClient) -> None:
    response = log_in(visitor, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert visitor.get("/settings").status_code == 200


def test_logged_in_user_skips_the_login_page(client: AppClient) -> None:
    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_issues_a_new_session_cookie(visitor: AppClient) -> None:
    log_in(visitor)
    first = visitor.cookies.get("__Host-makerhub_session")

    visitor.cookies.clear()
    log_in(visitor)

    assert first
    assert visitor.cookies.get("__Host-makerhub_session") != first


# --- Test case 8: same message for wrong name and wrong password -----------------------


@pytest.mark.parametrize(
    ("username", "password"),
    [(ADMIN_NAME, "falsches-passwort"), ("unbekannt", ADMIN_PASSWORD), ("", ""), (ADMIN_NAME, "")],
)
def test_wrong_login_gives_one_neutral_message(
    visitor: AppClient, username: str, password: str
) -> None:
    response = log_in(visitor, username=username, password=password)

    assert response.status_code == 401
    assert DE["login.failed"] in response.text
    assert visitor.get("/settings", follow_redirects=False).status_code == 303


def test_name_stays_in_the_form_after_a_failed_login(visitor: AppClient) -> None:
    response = log_in(visitor, password="falsches-passwort")

    assert f'value="{ADMIN_NAME}"' in response.text
    assert "falsches-passwort" not in response.text


# --- Test case 9: lockout after 5 failed attempts --------------------------------------


def fail(client: AppClient, times: int) -> None:
    for _ in range(times):
        assert log_in(client, password="falsches-passwort").status_code == 401


def test_five_failures_lock_the_account_even_for_the_right_password(visitor: AppClient) -> None:
    fail(visitor, 5)

    response = log_in(visitor)

    assert response.status_code == 401
    assert DE["login.failed"] in response.text


def test_lock_ends_after_15_minutes(visitor: AppClient, clock) -> None:
    fail(visitor, 5)
    clock(timedelta(minutes=14, seconds=59))
    assert log_in(visitor).status_code == 401

    clock(timedelta(seconds=2))

    assert log_in(visitor, follow_redirects=False).status_code == 303


def test_four_failures_do_not_lock(visitor: AppClient) -> None:
    fail(visitor, 4)

    assert log_in(visitor, follow_redirects=False).status_code == 303


def test_successful_login_resets_the_counter(visitor: AppClient) -> None:
    fail(visitor, 4)
    log_in(visitor)
    visitor.cookies.clear()
    fail(visitor, 4)

    assert log_in(visitor, follow_redirects=False).status_code == 303


def test_after_the_lock_a_new_count_starts(visitor: AppClient, clock) -> None:
    fail(visitor, 5)
    clock(timedelta(minutes=16))
    fail(visitor, 4)

    assert log_in(visitor, follow_redirects=False).status_code == 303


def test_unknown_names_lock_nothing(visitor: AppClient) -> None:
    for _ in range(6):
        log_in(visitor, username="unbekannt")

    assert log_in(visitor, follow_redirects=False).status_code == 303


# --- Test case 10: password only as Argon2 hash ----------------------------------------


def test_password_is_stored_only_as_argon2_hash(client: AppClient, data_dir: Path) -> None:
    [user] = users()

    assert user.password_hash.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in user.password_hash
    assert ADMIN_PASSWORD.encode() not in (data_dir / DATABASE_FILE).read_bytes()


def test_real_hashing_uses_argon2id_with_secure_defaults() -> None:
    hasher = auth.make_password_hasher()
    assert hasher.type.name == "ID"
    assert hasher.memory_cost >= 19 * 1024


# --- Test case 11: logout --------------------------------------------------------------


def test_logout_ends_the_session(client: AppClient) -> None:
    response = client.post("/logout", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_old_cookie_is_useless_after_logout(client: AppClient) -> None:
    old_cookie = client.cookies.get("__Host-makerhub_session")
    client.post("/logout", data={})

    with AppClient(client.app, cookies={"__Host-makerhub_session": old_cookie}) as thief:
        response = thief.get("/settings", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_logout_button_is_on_every_page(client: AppClient) -> None:
    for path in ("/", "/settings"):
        html = client.get(path).text
        assert re.search(r'<form\b[^>]*method="post"[^>]*action="/logout"', html)
        assert DE["nav.logout"] in html


def test_session_is_stored_only_as_fingerprint(client: AppClient, data_dir: Path) -> None:
    token = client.cookies.get("__Host-makerhub_session")

    with sqlite3.connect(data_dir / DATABASE_FILE) as connection:
        stored_ids = [row[0] for row in connection.execute("SELECT id FROM login_sessions")]

    assert stored_ids
    assert token not in stored_ids


# --- Test case 12: automatic logout after 8 hours without activity ---------------------


def test_session_ends_after_8_hours_without_activity(client: AppClient, clock) -> None:
    clock(timedelta(hours=8, minutes=1))

    response = client.get("/settings", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_activity_keeps_the_session_alive(client: AppClient, clock) -> None:
    for _ in range(3):
        clock(timedelta(hours=7, minutes=59))
        assert client.get("/settings", follow_redirects=False).status_code == 200


# --- Test case 13 + 16: cookie properties ----------------------------------------------


def session_cookie_header(response) -> str:
    headers = [h for h in response.headers.get_list("set-cookie") if "makerhub_session=" in h]
    assert len(headers) == 1
    return headers[0]


def test_session_cookie_is_protected(visitor: AppClient) -> None:
    header = session_cookie_header(log_in(visitor, follow_redirects=False))

    assert header.startswith("__Host-makerhub_session=")
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    assert "Domain" not in header


def test_form_token_cookie_before_login_is_protected(visitor: AppClient) -> None:
    visitor.cookies.clear()
    response = visitor.get("/login")
    [header] = [h for h in response.headers.get_list("set-cookie") if "makerhub_csrf=" in h]

    assert header.startswith("__Host-makerhub_csrf=")
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=lax" in header


def test_insecure_cookies_can_be_allowed_for_home_networks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAKERHUB_INSECURE_COOKIES", "true")

    with signed_in_client(base_url="http://testserver") as client:
        assert client.get("/settings").status_code == 200
        client.cookies.clear()
        header = session_cookie_header(log_in(client, follow_redirects=False))

    assert header.startswith("makerhub_session=")
    assert "Secure" not in header
    assert "HttpOnly" in header


@pytest.mark.parametrize(
    ("value", "expected"), [("", False), ("false", False), ("true", True), (" TRUE ", True)]
)
def test_insecure_cookies_setting(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv("MAKERHUB_INSECURE_COOKIES", value)

    assert Settings.from_env().insecure_cookies is expected


@pytest.mark.parametrize("value", ["yes", "1", "ja", "wahr"])
def test_invalid_insecure_cookies_value_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("MAKERHUB_INSECURE_COOKIES", value)

    with pytest.raises(ConfigError) as error:
        create_app()

    assert "MAKERHUB_INSECURE_COOKIES" in str(error.value)


def test_insecure_cookies_are_explained_in_env_example() -> None:
    example = (APP_DIR.parent / ".env.example").read_text(encoding="utf-8")

    assert "# MAKERHUB_INSECURE_COOKIES=false" in example


# --- Test case 14: every form carries a token ------------------------------------------


@pytest.mark.parametrize("path", ["/", "/settings"])
def test_forms_carry_the_session_token(client: AppClient, path: str) -> None:
    html = client.get(path).text

    forms = re.findall(r"<form\b.*?</form>", html, re.DOTALL)
    assert forms
    for form in forms:
        assert f'name="csrf_token" value="{client.csrf_token}"' in form


@pytest.mark.parametrize("token", [None, "", "falsch"])
def test_settings_without_valid_token_are_rejected(client: AppClient, token: str | None) -> None:
    form = valid_form(markup="99")
    if token is not None:
        form["csrf_token"] = token

    response = client.request("POST", "/settings", data=form)

    assert response.status_code == 403
    assert response.text == DE["error.forbidden"]
    assert stored_markup() == Decimal("30")


def test_token_of_another_session_is_rejected(client: AppClient) -> None:
    with AppClient(client.app) as other:
        log_in(other)
        other_token = csrf_from(other.get("/").text)

    response = client.request(
        "POST", "/settings", data=valid_form(markup="99", csrf_token=other_token)
    )

    assert response.status_code == 403
    assert stored_markup() == Decimal("30")


def test_settings_with_valid_token_are_saved(client: AppClient) -> None:
    client.post("/settings", data=valid_form(markup="99"))

    assert stored_markup() == Decimal("99")


@pytest.mark.parametrize("token", [None, "falsch"])
def test_login_without_valid_token_is_rejected(visitor: AppClient, token: str | None) -> None:
    visitor.get("/login")
    form = {"username": ADMIN_NAME, "password": ADMIN_PASSWORD}
    if token is not None:
        form["csrf_token"] = token

    response = visitor.request("POST", "/login", data=form)

    assert response.status_code == 403
    assert visitor.get("/settings", follow_redirects=False).status_code == 303


def test_setup_without_valid_token_is_rejected(anon_client: AppClient) -> None:
    response = anon_client.request(
        "POST", "/setup", data=setup_form(anon_client, csrf_token="falsch")
    )

    assert response.status_code == 403
    assert users() == []


def test_requests_without_form_data_are_rejected(client: AppClient) -> None:
    response = client.request("POST", "/settings", json=valid_form(csrf_token=client.csrf_token))

    assert response.status_code == 403


def test_foreign_site_is_still_rejected_with_valid_token(client: AppClient) -> None:
    response = client.post(
        "/settings", data=valid_form(markup="99"), headers={"Sec-Fetch-Site": "cross-site"}
    )

    assert response.status_code == 403
    assert stored_markup() == Decimal("30")


# --- Test case 15: logout only via the button ------------------------------------------


def test_logout_link_does_not_log_out(client: AppClient) -> None:
    assert client.get("/logout").status_code == 405

    assert client.get("/settings", follow_redirects=False).status_code == 200


def test_logout_without_token_does_not_log_out(client: AppClient) -> None:
    response = client.request("POST", "/logout")

    assert response.status_code == 403
    assert client.get("/settings", follow_redirects=False).status_code == 200


# --- Translations ----------------------------------------------------------------------


def test_login_page_in_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_LANGUAGE", "en")
    en = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))

    with AppClient(create_app()) as client:
        html = client.get("/setup").text

    assert en["setup.title"] in html
    assert '<html lang="en"' in html


# --- Review findings: parallel attempts, answer time, second tab -----------------------


def test_parallel_failures_still_lock_the_account(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "parallel.db")
    run_migrations(engine)
    new_session = sessionmaker(engine)
    with new_session() as db, db.begin():
        auth.create_user(db, ADMIN_NAME, ADMIN_PASSWORD, UserRole.ADMIN)
    start = threading.Barrier(20)

    def attempt() -> None:
        start.wait()
        auth.log_in(new_session, ADMIN_NAME, "falsches-passwort")

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert auth.log_in(new_session, ADMIN_NAME, ADMIN_PASSWORD) is None
    with new_session() as db:
        assert db.query(User).one().locked_until is not None


def test_locked_account_takes_the_same_effort_as_a_wrong_password(
    visitor: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail(visitor, 5)
    checks = []
    real_verify = auth.verify_password
    monkeypatch.setattr(
        auth, "verify_password", lambda *args: checks.append(1) or real_verify(*args)
    )

    log_in(visitor)

    assert len(checks) == 1


def test_old_login_form_in_a_second_tab_leads_to_the_app(visitor: AppClient) -> None:
    old_form = {
        "csrf_token": csrf_from(visitor.get("/login").text),
        "username": ADMIN_NAME,
        "password": ADMIN_PASSWORD,
    }
    log_in(visitor)

    response = visitor.request("POST", "/login", data=old_form, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
