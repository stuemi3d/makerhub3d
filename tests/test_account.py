"""My account: change the own password, duty to change an initial password (issue #32)."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.main import create_app
from app.models import AppSettings, LoginSession, User
from tests.auth import (
    ADMIN_NAME,
    ADMIN_PASSWORD,
    EMPLOYEE_NAME,
    OTHER_PASSWORD,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
    csrf_from,
    log_in,
)
from tests.forms import valid_form

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
NEW_PASSWORD = "mein-neues-passwort"


def user_named(username: str) -> User:
    with Session(make_engine(Settings.from_env().database_path)) as session:
        return session.query(User).filter_by(username=username).one()


def session_count(username: str) -> int:
    user_id = user_named(username).id
    with Session(make_engine(Settings.from_env().database_path)) as session:
        return session.query(LoginSession).filter_by(user_id=user_id).count()


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


def change_form(current: str = ADMIN_PASSWORD, new: str = NEW_PASSWORD, repeat: str | None = None):
    return {
        "current_password": current,
        "password": new,
        "password_repeat": new if repeat is None else repeat,
    }


def can_log_in(username: str, password: str) -> bool:
    with AppClient(create_app()) as visitor:
        return log_in(visitor, username, password, follow_redirects=False).status_code == 303


def nav_of(html: str) -> str:
    return re.search(r"<nav\b.*?</nav>", html, re.DOTALL).group(0)


# --- Test case 1: everybody reaches "my account" ---------------------------------------


@pytest.mark.parametrize("role", ["employee", "read_only", "admin"])
def test_every_role_reaches_my_account(client: AppClient, role: str) -> None:
    add_user(EMPLOYEE_NAME, role)
    with client_as(EMPLOYEE_NAME) as user:
        start = user.get("/")
        assert re.search(rf'<a href="/account"[^>]*>{EMPLOYEE_NAME}</a>', nav_of(start.text))

        page = user.get("/account")

    assert page.status_code == 200
    assert DE["account.title"] in page.text
    assert DE[f"role.{role}"] in page.text
    for name in ("current_password", "password", "password_repeat"):
        assert re.search(rf'<input\b[^>]*type="password"[^>]*name="{name}"', page.text)


# --- Test case 2: change the password --------------------------------------------------


def test_password_is_changed(client: AppClient) -> None:
    response = client.post("/account/password", data=change_form(), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/account?changed=1"
    assert DE["account.changed"] in client.get(response.headers["location"]).text
    assert can_log_in(ADMIN_NAME, NEW_PASSWORD)
    assert not can_log_in(ADMIN_NAME, ADMIN_PASSWORD)


def test_new_password_is_stored_as_argon2_hash(client: AppClient) -> None:
    client.post("/account/password", data=change_form())

    stored = user_named(ADMIN_NAME).password_hash
    assert stored.startswith("$argon2id$")
    assert NEW_PASSWORD not in stored


# --- Test case 3: invalid input --------------------------------------------------------


@pytest.mark.parametrize(
    ("form", "field", "message_key"),
    [
        (change_form(current="falsches-passwort"), "current_password",
         "account.error.current_wrong"),
        (change_form(current=""), "current_password", "account.error.current_wrong"),
        (change_form(new="zu-kurz"), "password", "user.error.password_too_short"),
        (change_form(new="x" * 1025), "password", "user.error.password_too_long"),
        (change_form(repeat="etwas-anderes-langes"), "password_repeat",
         "user.error.password_mismatch"),
        (change_form(new=ADMIN_PASSWORD), "password", "account.error.same"),
    ],
)  # fmt: skip
def test_invalid_change_keeps_the_password(
    client: AppClient, form: dict, field: str, message_key: str
) -> None:
    response = client.post("/account/password", data=form)

    assert response.status_code == 422
    assert field_error(response.text, field) == DE[message_key]
    assert can_log_in(ADMIN_NAME, ADMIN_PASSWORD)


# --- Test case 4: other devices are logged out -----------------------------------------


def test_other_devices_are_logged_out(client: AppClient) -> None:
    with AppClient(client.app) as other_device:
        log_in(other_device)
        assert other_device.get("/").status_code == 200

        client.post("/account/password", data=change_form())

        assert other_device.get("/", follow_redirects=False).status_code == 303
    assert client.get("/account").status_code == 200
    assert session_count(ADMIN_NAME) == 1


def test_other_users_stay_logged_in(client: AppClient) -> None:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        client.post("/account/password", data=change_form())

        assert employee.get("/").status_code == 200


# --- Test case 5: wrong current password counts as failed login ------------------------


def test_wrong_current_password_counts_as_failed_login(client: AppClient) -> None:
    for _ in range(4):
        client.post("/account/password", data=change_form(current="falsches-passwort"))

    assert client.get("/account").status_code == 200
    assert user_named(ADMIN_NAME).failed_logins == 4

    response = client.post(
        "/account/password", data=change_form(current="falsches-passwort"), follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303
    assert session_count(ADMIN_NAME) == 0
    assert not can_log_in(ADMIN_NAME, ADMIN_PASSWORD)
    assert user_named(ADMIN_NAME).locked_until is not None


def test_failures_on_form_and_login_page_together_log_out(client: AppClient) -> None:
    for _ in range(4):
        client.post("/account/password", data=change_form(current="falsches-passwort"))
    with AppClient(client.app) as stranger:
        log_in(stranger, ADMIN_NAME, "falsches-passwort")

    response = client.post("/account/password", data=change_form(), follow_redirects=False)

    assert response.headers["location"] == "/login"
    assert session_count(ADMIN_NAME) == 0
    assert can_log_in(ADMIN_NAME, ADMIN_PASSWORD) is False


def test_no_guesses_while_locked(client: AppClient) -> None:
    old_hash = user_named(ADMIN_NAME).password_hash
    with AppClient(client.app) as stranger:
        for _ in range(5):
            log_in(stranger, ADMIN_NAME, "falsches-passwort")

    response = client.post("/account/password", data=change_form(), follow_redirects=False)

    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303
    # Even the right current password did not help: the password is unchanged.
    assert user_named(ADMIN_NAME).password_hash == old_hash


def test_correct_current_password_resets_the_count(client: AppClient) -> None:
    for _ in range(4):
        client.post("/account/password", data=change_form(current="falsches-passwort"))
    client.post("/account/password", data=change_form(new="zu-kurz"))

    assert user_named(ADMIN_NAME).failed_logins == 0


# --- Test case 6: passwords are never sent back ----------------------------------------


def test_passwords_are_never_sent_back(client: AppClient) -> None:
    response = client.post("/account/password", data=change_form(repeat="etwas-anderes-langes"))

    for secret in (ADMIN_PASSWORD, NEW_PASSWORD, "etwas-anderes-langes"):
        assert secret not in response.text


# --- Test cases 7-9: duty to change an initial password --------------------------------


@pytest.fixture
def newcomer(client: AppClient) -> AppClient:
    """A user created by the admin through the user management, logged in once."""
    client.post(
        "/settings/users/new",
        data={
            "username": EMPLOYEE_NAME,
            "role": "employee",
            "password": OTHER_PASSWORD,
            "password_repeat": OTHER_PASSWORD,
        },
    )
    with AppClient(create_app()) as user:
        assert (
            log_in(user, EMPLOYEE_NAME, OTHER_PASSWORD, follow_redirects=False).status_code == 303
        )
        user.csrf_token = csrf_from(user.get("/account").text)
        yield user


@pytest.mark.parametrize("path", ["/", "/settings", "/does-not-exist", "/account?changed=1"])
def test_new_user_is_sent_to_change_the_password(newcomer: AppClient, path: str) -> None:
    response = newcomer.get(path, follow_redirects=False)

    if path.startswith("/account"):
        assert response.status_code == 200
        assert DE["account.must_change"] in response.text
    else:
        assert response.status_code == 303
        assert response.headers["location"] == "/account"


def test_page_explains_the_duty_and_hides_the_menu(newcomer: AppClient) -> None:
    html = newcomer.get("/account").text

    assert DE["account.must_change"] in html
    assert 'href="/"' not in nav_of(html)
    assert re.search(r'<form\b[^>]*method="post"[^>]*action="/logout"', html)


def test_new_user_can_log_out(newcomer: AppClient) -> None:
    response = newcomer.post("/logout", data={}, follow_redirects=False)

    assert response.headers["location"] == "/login"


def test_new_admin_cannot_send_other_forms_before_changing(client: AppClient) -> None:
    client.post(
        "/settings/users/new",
        data={
            "username": "neue-chefin",
            "role": "admin",
            "password": OTHER_PASSWORD,
            "password_repeat": OTHER_PASSWORD,
        },
    )
    with AppClient(create_app()) as newcomer:
        log_in(newcomer, "neue-chefin", OTHER_PASSWORD)
        newcomer.csrf_token = csrf_from(newcomer.get("/account").text)

        response = newcomer.post("/settings", data=valid_form(markup="99"), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/account"
    with Session(make_engine(Settings.from_env().database_path)) as session:
        assert session.get(AppSettings, 1).markup == Decimal("30")


def test_duty_also_applies_after_reset_by_admin(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    client.post(
        f"/settings/users/{user_id}/password",
        data={"password": OTHER_PASSWORD + "!", "password_repeat": OTHER_PASSWORD + "!"},
    )

    with AppClient(create_app()) as user:
        log_in(user, EMPLOYEE_NAME, OTHER_PASSWORD + "!")
        response = user.get("/", follow_redirects=False)

    assert response.headers["location"] == "/account"


def test_after_changing_the_duty_is_done(newcomer: AppClient) -> None:
    response = newcomer.post(
        "/account/password", data=change_form(current=OTHER_PASSWORD), follow_redirects=False
    )

    assert response.status_code == 303
    assert newcomer.get("/").status_code == 200
    assert DE["account.must_change"] not in newcomer.get("/account").text
    assert user_named(EMPLOYEE_NAME).must_change_password is False


def test_keeping_the_initial_password_is_not_possible(newcomer: AppClient) -> None:
    response = newcomer.post(
        "/account/password",
        data=change_form(current=OTHER_PASSWORD, new=OTHER_PASSWORD),
    )

    assert field_error(response.text, "password") == DE["account.error.same"]
    assert newcomer.get("/", follow_redirects=False).status_code == 303


# --- Test case 10: the first admin chose the password ----------------------------------


def test_first_admin_has_no_duty(client: AppClient) -> None:
    assert user_named(ADMIN_NAME).must_change_password is False
    assert client.get("/").status_code == 200


# --- Test case 11: the user list shows who still has an initial password ---------------


def test_user_list_shows_initial_passwords(client: AppClient) -> None:
    add_user(EMPLOYEE_NAME, "employee", must_change_password=True)
    add_user(READER_NAME, "read_only")

    html = client.get("/settings/users").text

    rows = {
        name: row
        for row in re.findall(r"<tr\b.*?</tr>", html, re.DOTALL)
        for name in (EMPLOYEE_NAME, READER_NAME)
        if f">{name}<" in row
    }
    assert DE["user.status.initial_password"] in rows[EMPLOYEE_NAME]
    assert DE["user.status.initial_password"] not in rows[READER_NAME]


# --- Test case 12: form token ----------------------------------------------------------


def test_account_form_carries_the_token(client: AppClient) -> None:
    html = client.get("/account").text

    for form in re.findall(r"<form\b.*?</form>", html, re.DOTALL):
        assert f'name="csrf_token" value="{client.csrf_token}"' in form


@pytest.mark.parametrize("token", [None, "falsch"])
def test_change_without_token_is_rejected(client: AppClient, token: str | None) -> None:
    form = change_form()
    if token is not None:
        form["csrf_token"] = token

    response = client.request("POST", "/account/password", data=form)

    assert response.status_code == 403
    assert can_log_in(ADMIN_NAME, ADMIN_PASSWORD)
