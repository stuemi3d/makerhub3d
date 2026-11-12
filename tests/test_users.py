"""User management by the admin (issue #28, test cases 6-14)."""

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app import auth
from app.config import Settings
from app.db import make_engine
from app.main import create_app
from app.models import LoginSession, User
from tests.auth import (
    ADMIN_NAME,
    ADMIN_PASSWORD,
    EMPLOYEE_NAME,
    OTHER_PASSWORD,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
    log_in,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
NEW_PASSWORD = "ganz-neues-start-passwort"


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


def user_named(username: str) -> User | None:
    with db() as session:
        return session.query(User).filter_by(username=username).one_or_none()


def session_count(user_id: int) -> int:
    with db() as session:
        return session.query(LoginSession).filter_by(user_id=user_id).count()


def admin_id() -> int:
    return user_named(ADMIN_NAME).id


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


def new_user_form(**overrides: str) -> dict[str, str]:
    form = {
        "username": EMPLOYEE_NAME,
        "role": "employee",
        "password": OTHER_PASSWORD,
        "password_repeat": OTHER_PASSWORD,
    }
    form.update(overrides)
    return form


def can_log_in(username: str, password: str = OTHER_PASSWORD) -> bool:
    with AppClient(create_app()) as visitor:
        return log_in(visitor, username, password, follow_redirects=False).status_code == 303


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    state = {"now": auth.utcnow()}
    monkeypatch.setattr(auth, "utcnow", lambda: state["now"])

    def advance(delta: timedelta) -> None:
        state["now"] = state["now"] + delta

    return advance


# --- Test case 6: list of users --------------------------------------------------------


def test_list_shows_name_role_and_state(client: AppClient) -> None:
    add_user(EMPLOYEE_NAME, "employee")
    reader_id = add_user(READER_NAME, "read_only")
    client.post(f"/settings/users/{reader_id}", data={"role": "read_only", "active": "0"})

    html = client.get("/settings/users").text

    rows = {
        name: row
        for row in re.findall(r"<tr\b.*?</tr>", html, re.DOTALL)
        for name in (ADMIN_NAME, EMPLOYEE_NAME, READER_NAME)
        if f">{name}<" in row
    }
    assert DE["role.admin"] in rows[ADMIN_NAME]
    assert DE["role.employee"] in rows[EMPLOYEE_NAME]
    assert DE["role.read_only"] in rows[READER_NAME]
    assert DE["user.status.active"] in rows[EMPLOYEE_NAME]
    assert DE["user.status.inactive"] in rows[READER_NAME]


def test_list_shows_locked_users(client: AppClient, clock) -> None:
    add_user(EMPLOYEE_NAME, "employee")
    with AppClient(client.app) as visitor:
        for _ in range(5):
            log_in(visitor, EMPLOYEE_NAME, "falsches-passwort")
    clock(timedelta(minutes=3))

    html = client.get("/settings/users").text

    assert DE["user.status.locked"].format(minutes=12) in html


def test_settings_page_links_to_users(client: AppClient) -> None:
    assert 'href="/settings/users"' in client.get("/settings").text


# --- Test case 7: create a user --------------------------------------------------------


def test_admin_creates_a_user_who_can_log_in(client: AppClient) -> None:
    response = client.post("/settings/users/new", data=new_user_form(), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/settings/users"
    user = user_named(EMPLOYEE_NAME)
    assert user.role == "employee"
    assert user.active is True
    assert user.password_hash.startswith("$argon2id$")
    assert can_log_in(EMPLOYEE_NAME)


@pytest.mark.parametrize("role", ["admin", "employee", "read_only"])
def test_every_role_can_be_chosen(client: AppClient, role: str) -> None:
    client.post("/settings/users/new", data=new_user_form(role=role))

    assert user_named(EMPLOYEE_NAME).role == role


def test_new_user_form_offers_the_three_roles(client: AppClient) -> None:
    html = client.get("/settings/users/new").text

    assert re.findall(r'<option value="([^"]+)"', html) == ["admin", "employee", "read_only"]


# --- Test case 8: invalid input --------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field", "message_key"),
    [
        ({"username": ADMIN_NAME}, "username", "user.error.taken"),
        ({"username": ADMIN_NAME.upper()}, "username", "user.error.taken"),
        ({"username": f"  {ADMIN_NAME.title()} "}, "username", "user.error.taken"),
        ({"username": ""}, "username", "user.error.required"),
        ({"username": "x" * 51}, "username", "user.error.username_too_long"),
        ({"role": "chef"}, "role", "user.error.choice"),
        ({"role": ""}, "role", "user.error.choice"),
        ({"password": "zu-kurz", "password_repeat": "zu-kurz"}, "password",
         "user.error.password_too_short"),
        ({"password_repeat": "etwas-anderes-langes"}, "password_repeat",
         "user.error.password_mismatch"),
    ],
)  # fmt: skip
def test_invalid_new_user_is_not_created(
    client: AppClient, overrides: dict, field: str, message_key: str
) -> None:
    response = client.post("/settings/users/new", data=new_user_form(**overrides))

    assert response.status_code == 422
    assert field_error(response.text, field) == DE[message_key]
    with db() as session:
        assert session.query(User).count() == 1


def test_passwords_are_not_shown_again(client: AppClient) -> None:
    response = client.post("/settings/users/new", data=new_user_form(username=""))

    assert OTHER_PASSWORD not in response.text
    assert re.search(r'<option value="employee" selected>', response.text)


# --- Test case 9: change the role ------------------------------------------------------


def test_admin_changes_the_role(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    response = client.post(
        f"/settings/users/{user_id}", data={"role": "read_only", "active": "1"},
        follow_redirects=False,
    )  # fmt: skip

    assert response.status_code == 303
    assert user_named(EMPLOYEE_NAME).role == "read_only"


def test_edit_page_shows_current_values(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "read_only")

    html = client.get(f"/settings/users/{user_id}").text

    assert EMPLOYEE_NAME in html
    assert re.search(r'<option value="read_only" selected>', html)


@pytest.mark.parametrize(
    "form", [{"role": "chef", "active": "1"}, {"role": "admin", "active": "vielleicht"}, {}]
)
def test_invalid_changes_are_rejected(client: AppClient, form: dict) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    response = client.post(f"/settings/users/{user_id}", data=form)

    assert response.status_code == 422
    user = user_named(EMPLOYEE_NAME)
    assert (user.role, user.active) == ("employee", True)


def test_unknown_user_is_not_found(client: AppClient) -> None:
    assert client.get("/settings/users/999").status_code == 404
    form = {"role": "admin", "active": "1"}
    assert client.post("/settings/users/999", data=form).status_code == 404


# --- Test case 10: deactivate and reactivate -------------------------------------------


def test_deactivated_user_cannot_log_in_and_is_logged_out(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        client.post(f"/settings/users/{user_id}", data={"role": "employee", "active": "0"})

        assert employee.get("/", follow_redirects=False).status_code == 303

    with AppClient(client.app) as visitor:
        response = log_in(visitor, EMPLOYEE_NAME, OTHER_PASSWORD)
    assert response.status_code == 401
    assert DE["login.failed"] in response.text
    assert session_count(user_id) == 0
    assert user_named(EMPLOYEE_NAME) is not None


def test_reactivated_user_can_log_in_again(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    client.post(f"/settings/users/{user_id}", data={"role": "employee", "active": "0"})

    client.post(f"/settings/users/{user_id}", data={"role": "employee", "active": "1"})

    assert can_log_in(EMPLOYEE_NAME)


def test_there_is_no_delete(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    for method in ("DELETE", "POST"):
        response = client.request(method, f"/settings/users/{user_id}/delete")
        assert response.status_code in {403, 404, 405}
    assert user_named(EMPLOYEE_NAME) is not None


# --- Test case 11: reset the password --------------------------------------------------


def reset(client: AppClient, user_id: int, password: str = NEW_PASSWORD, **kwargs):
    form = {"password": password, "password_repeat": password}
    return client.post(f"/settings/users/{user_id}/password", data=form, **kwargs)


def test_reset_password_replaces_the_old_one(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    response = reset(client, user_id, follow_redirects=False)

    assert response.status_code == 303
    assert can_log_in(EMPLOYEE_NAME, NEW_PASSWORD)
    assert not can_log_in(EMPLOYEE_NAME, OTHER_PASSWORD)


def test_reset_password_ends_all_logins_of_that_user(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        reset(client, user_id)

        assert employee.get("/", follow_redirects=False).status_code == 303
    # The admin stays logged in.
    assert client.get("/settings/users").status_code == 200


def test_reset_password_lifts_a_lock(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    with AppClient(client.app) as visitor:
        for _ in range(5):
            log_in(visitor, EMPLOYEE_NAME, "falsches-passwort")

    reset(client, user_id)

    assert can_log_in(EMPLOYEE_NAME, NEW_PASSWORD)


@pytest.mark.parametrize(
    ("password", "repeat", "field", "message_key"),
    [
        ("zu-kurz", "zu-kurz", "password", "user.error.password_too_short"),
        (NEW_PASSWORD, NEW_PASSWORD + "x", "password_repeat", "user.error.password_mismatch"),
    ],
)
def test_invalid_reset_changes_nothing(
    client: AppClient, password: str, repeat: str, field: str, message_key: str
) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    response = client.post(
        f"/settings/users/{user_id}/password",
        data={"password": password, "password_repeat": repeat},
    )

    assert response.status_code == 422
    assert field_error(response.text, field) == DE[message_key]
    assert can_log_in(EMPLOYEE_NAME, OTHER_PASSWORD)


# --- Test case 12: an admin cannot lock themselves out ---------------------------------


@pytest.mark.parametrize(
    "form", [{"role": "employee", "active": "1"}, {"role": "admin", "active": "0"}]
)
def test_admin_cannot_demote_or_deactivate_themselves(client: AppClient, form: dict) -> None:
    response = client.post(f"/settings/users/{admin_id()}", data=form)

    assert response.status_code == 422
    assert DE["user.error.self"] in response.text
    admin = user_named(ADMIN_NAME)
    assert (admin.role, admin.active) == ("admin", True)
    assert client.get("/settings").status_code == 200


def test_own_edit_page_offers_no_role_or_state_fields(client: AppClient) -> None:
    html = client.get(f"/settings/users/{admin_id()}").text

    assert 'name="role"' not in html
    assert 'name="active"' not in html
    assert DE["user.hint.self"] in html


def test_a_second_admin_may_demote_the_first(client: AppClient) -> None:
    add_user("zweiter-admin", "admin")
    with client_as("zweiter-admin") as second:
        response = second.post(
            f"/settings/users/{admin_id()}", data={"role": "employee", "active": "1"}
        )

    assert response.status_code == 200
    assert user_named(ADMIN_NAME).role == "employee"


def test_last_active_admin_always_remains(client: AppClient) -> None:
    second_id = add_user("zweiter-admin", "admin")
    with client_as("zweiter-admin") as second:
        # The second admin takes away the first admin's role ...
        second.post(f"/settings/users/{admin_id()}", data={"role": "employee", "active": "1"})
        # ... the first one can no longer change anything ...
        form = {"role": "employee", "active": "0"}
        assert client.post(f"/settings/users/{second_id}", data=form).status_code == 403
        # ... and the second, now the only admin, cannot remove themselves.
        assert second.post(f"/settings/users/{second_id}", data=form).status_code == 422

    second_admin = user_named("zweiter-admin")
    assert (second_admin.role, second_admin.active) == ("admin", True)


# --- Test case 13: names ignore upper and lower case at login --------------------------


@pytest.mark.parametrize("typed", [ADMIN_NAME.upper(), ADMIN_NAME.title(), f" {ADMIN_NAME} "])
def test_login_ignores_case(client: AppClient, typed: str) -> None:
    assert can_log_in(typed, ADMIN_PASSWORD)


def test_name_is_shown_as_created(client: AppClient) -> None:
    client.post("/settings/users/new", data=new_user_form(username="Anna-Lena"))

    assert can_log_in("anna-lena")
    assert ">Anna-Lena<" in client.get("/settings/users").text


def test_unicode_names_ignore_case_too(client: AppClient) -> None:
    client.post("/settings/users/new", data=new_user_form(username="Jörg"))

    response = client.post("/settings/users/new", data=new_user_form(username="JÖRG"))

    assert field_error(response.text, "username") == DE["user.error.taken"]
    assert can_log_in("jÖrg")


# --- Test case 14: form tokens and escaping --------------------------------------------


@pytest.mark.parametrize("path", ["/settings/users/new", "/settings/users/{id}"])
def test_user_forms_carry_the_token(client: AppClient, path: str) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    html = client.get(path.format(id=user_id)).text

    forms = re.findall(r"<form\b.*?</form>", html, re.DOTALL)
    assert len(forms) >= 2
    for form in forms:
        assert f'name="csrf_token" value="{client.csrf_token}"' in form


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/settings/users/new", new_user_form()),
        ("/settings/users/{id}", {"role": "admin", "active": "1"}),
        ("/settings/users/{id}/password",
         {"password": NEW_PASSWORD, "password_repeat": NEW_PASSWORD}),
    ],
)  # fmt: skip
def test_user_forms_without_token_are_rejected(client: AppClient, path: str, data: dict) -> None:
    user_id = add_user(READER_NAME, "read_only")

    response = client.request("POST", path.format(id=user_id), data=data)

    assert response.status_code == 403
    assert user_named(EMPLOYEE_NAME) is None
    assert user_named(READER_NAME).role == "read_only"
    assert can_log_in(READER_NAME)


def test_names_are_escaped(client: AppClient) -> None:
    client.post("/settings/users/new", data=new_user_form(username="<b>fett</b>"))

    html = client.get("/settings/users").text

    assert "<b>fett</b>" not in html
    assert "&lt;b&gt;fett&lt;/b&gt;" in html
