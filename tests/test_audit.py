"""Change log and login log (issue #29, test cases 1-15)."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app import auth
from app.config import ConfigError, Settings
from app.db import make_engine
from app.main import create_app
from app.models import ChangeLogEntry, LoginLogEntry, User
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
from tests.forms import valid_form

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
NEW_PASSWORD = "ein-ganz-neues-passwort"


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


def changes() -> list[ChangeLogEntry]:
    with db() as session:
        return list(session.query(ChangeLogEntry).order_by(ChangeLogEntry.id))


def logins() -> list[LoginLogEntry]:
    with db() as session:
        return list(session.query(LoginLogEntry).order_by(LoginLogEntry.id))


def user_id(username: str) -> int:
    with db() as session:
        return session.query(User).filter_by(username=username).one().id


def log_rows(html: str) -> list[str]:
    body = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL)
    return re.findall(r"<tr\b.*?</tr>", body.group(1), re.DOTALL) if body else []


def database_text(data_dir: Path) -> str:
    return (data_dir / "makerhub3d.db").read_bytes().decode("utf-8", errors="ignore")


# --- Test case 1 + 2: settings ---------------------------------------------------------


def test_changed_settings_are_logged_with_old_and_new_value(client: AppClient) -> None:
    before = len(changes())

    client.post("/settings", data=valid_form(electricity_price="0,35"))

    [entry] = changes()[before:]
    assert entry.user_id == user_id(ADMIN_NAME)
    assert entry.action == "settings.changed"
    assert json.loads(entry.details) == [
        {"field": "electricity_price", "old": "0.30", "new": "0.35"}
    ]
    html = client.get("/settings/log").text
    first = log_rows(html)[0]
    assert ADMIN_NAME in first
    assert DE["log.action.settings.changed"] in first
    assert f"{DE['settings.field.electricity_price']}: 0,30 → 0,35" in first


def test_only_changed_fields_are_listed(client: AppClient) -> None:
    client.post("/settings", data=valid_form(markup="45", rounding="whole_euro"))

    details = json.loads(changes()[-1].details)
    assert [item["field"] for item in details] == ["markup", "rounding"]
    first = log_rows(client.get("/settings/log").text)[0]
    assert DE["settings.rounding.whole_euro"] in first


def test_saving_without_change_writes_nothing(client: AppClient) -> None:
    before = len(changes())

    client.post("/settings", data=valid_form())

    assert len(changes()) == before


# --- Test case 3: user management ------------------------------------------------------


def test_user_management_is_logged(client: AppClient) -> None:
    before = len(changes())
    client.post(
        "/settings/users/new",
        data={
            "username": EMPLOYEE_NAME,
            "role": "employee",
            "password": OTHER_PASSWORD,
            "password_repeat": OTHER_PASSWORD,
        },
    )
    target = user_id(EMPLOYEE_NAME)
    client.post(f"/settings/users/{target}", data={"role": "read_only", "active": "1"})
    client.post(f"/settings/users/{target}", data={"role": "read_only", "active": "0"})
    client.post(f"/settings/users/{target}", data={"role": "read_only", "active": "1"})
    client.post(
        f"/settings/users/{target}/password",
        data={"password": NEW_PASSWORD, "password_repeat": NEW_PASSWORD},
    )

    entries = changes()[before:]
    assert [e.action for e in entries] == [
        "user.created",
        "user.role_changed",
        "user.deactivated",
        "user.activated",
        "user.password_reset",
    ]
    assert all(e.subject == EMPLOYEE_NAME for e in entries)
    assert json.loads(entries[1].details) == [
        {"field": "role", "old": "employee", "new": "read_only"}
    ]
    first = log_rows(client.get("/settings/log").text)[3]
    assert DE["role.employee"] in first and DE["role.read_only"] in first


def test_saving_a_user_without_change_writes_nothing(client: AppClient) -> None:
    target = add_user(EMPLOYEE_NAME, "employee")
    before = len(changes())

    client.post(f"/settings/users/{target}", data={"role": "employee", "active": "1"})

    assert len(changes()) == before


# --- Test case 4: own password and first admin -----------------------------------------


def test_first_admin_and_own_password_are_logged(client: AppClient) -> None:
    assert changes()[0].action == "setup.admin_created"
    assert changes()[0].subject == ADMIN_NAME

    client.post(
        "/account/password",
        data={
            "current_password": ADMIN_PASSWORD,
            "password": NEW_PASSWORD,
            "password_repeat": NEW_PASSWORD,
        },
    )

    assert changes()[-1].action == "account.password_changed"
    assert changes()[-1].user_id == user_id(ADMIN_NAME)


# --- Test case 5: never passwords ------------------------------------------------------


def test_no_password_or_hash_in_any_log(client: AppClient, data_dir: Path) -> None:
    client.post(
        "/settings/users/new",
        data={
            "username": EMPLOYEE_NAME,
            "role": "employee",
            "password": OTHER_PASSWORD,
            "password_repeat": OTHER_PASSWORD,
        },
    )
    client.post(
        f"/settings/users/{user_id(EMPLOYEE_NAME)}/password",
        data={"password": NEW_PASSWORD, "password_repeat": NEW_PASSWORD},
    )

    for entry in changes():
        text = f"{entry.action} {entry.subject} {entry.details}"
        for secret in (ADMIN_PASSWORD, OTHER_PASSWORD, NEW_PASSWORD, "$argon2"):
            assert secret not in text
    page = client.get("/settings/log").text + client.get("/settings/log/logins").text
    for secret in (ADMIN_PASSWORD, OTHER_PASSWORD, NEW_PASSWORD, "$argon2"):
        assert secret not in page


# --- Test case 6: rejected input -------------------------------------------------------


def test_rejected_input_writes_nothing(client: AppClient) -> None:
    before = len(changes())

    client.post("/settings", data=valid_form(electricity_price="abc"))
    client.post("/settings/users/new", data={"username": "", "role": "chef"})
    client.post(f"/settings/users/{user_id(ADMIN_NAME)}", data={"role": "employee", "active": "1"})
    client.post(
        "/account/password",
        data={"current_password": ADMIN_PASSWORD, "password": "zu-kurz", "password_repeat": ""},
    )

    assert len(changes()) == before


# --- Test cases 7-9: login log ---------------------------------------------------------


def test_login_events_are_logged(client: AppClient) -> None:
    add_user(EMPLOYEE_NAME, "employee")
    before = len(logins())
    with AppClient(client.app) as visitor:
        log_in(visitor, EMPLOYEE_NAME, OTHER_PASSWORD)
        visitor.post("/logout", data={"csrf_token": ""})  # rejected: no valid token
        visitor.post("/logout", data={"csrf_token": csrf_from(visitor.get("/").text)})
        for _ in range(5):
            log_in(visitor, EMPLOYEE_NAME, "falsches-passwort")

    entries = logins()[before:]
    assert [e.event for e in entries] == [
        "success", "logout", "failed", "failed", "failed", "failed", "locked",
    ]  # fmt: skip
    assert {e.user_id for e in entries} == {user_id(EMPLOYEE_NAME)}


def test_unknown_names_are_not_stored(client: AppClient, data_dir: Path) -> None:
    with AppClient(client.app) as visitor:
        log_in(visitor, "mein-geheimes-passwort-im-namensfeld", "x")

    entry = logins()[-1]
    assert (entry.event, entry.user_id) == ("failed", None)
    assert "mein-geheimes-passwort-im-namensfeld" not in database_text(data_dir)
    first = log_rows(client.get("/settings/log/logins").text)[0]
    assert DE["log.unknown_user"] in first


def test_no_ip_addresses_are_stored() -> None:
    columns = set(LoginLogEntry.__table__.columns.keys()) | set(
        ChangeLogEntry.__table__.columns.keys()
    )
    assert not {name for name in columns if "ip" in name.split("_") or "address" in name}


def test_wrong_current_password_on_my_account_is_logged(client: AppClient) -> None:
    client.post(
        "/account/password",
        data={"current_password": "falsch-falsch", "password": NEW_PASSWORD,
              "password_repeat": NEW_PASSWORD},
    )  # fmt: skip

    assert logins()[-1].event == "wrong_current_password"


# --- Test case 10: retention -----------------------------------------------------------


def test_old_login_entries_are_deleted_change_log_stays(
    client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post("/settings", data=valid_form(markup="45"))
    changes_before = len(changes())
    later = auth.utcnow() + timedelta(days=366)
    monkeypatch.setattr(auth, "utcnow", lambda: later)

    with AppClient(client.app) as visitor:
        log_in(visitor)

    assert [e.event for e in logins()] == ["success"]
    assert len(changes()) == changes_before


def test_entries_younger_than_a_year_stay(
    client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = len(logins())
    later = auth.utcnow() + timedelta(days=364)
    monkeypatch.setattr(auth, "utcnow", lambda: later)

    with AppClient(client.app) as visitor:
        log_in(visitor)

    assert len(logins()) == before + 1


# --- Test case 11: admin only ----------------------------------------------------------


@pytest.mark.parametrize("role", ["employee", "read_only"])
@pytest.mark.parametrize("path", ["/settings/log", "/settings/log/logins"])
def test_only_admins_see_the_log(client: AppClient, role: str, path: str) -> None:
    add_user(EMPLOYEE_NAME, role)
    with client_as(EMPLOYEE_NAME) as other:
        response = other.get(path)

    assert response.status_code == 403
    assert client.get(path).status_code == 200


def test_settings_tabs_link_to_the_log(client: AppClient) -> None:
    assert 'href="/settings/log"' in client.get("/settings").text


# --- Test case 12: newest first, 50 per page -------------------------------------------


def test_newest_first_and_paging(client: AppClient) -> None:
    for value in range(60):
        client.post("/settings", data=valid_form(markup=str(value + 1)))

    page_one = client.get("/settings/log").text
    page_two = client.get("/settings/log?page=2").text

    rows_one, rows_two = log_rows(page_one), log_rows(page_two)
    assert len(rows_one) == 50
    assert "59 → 60" in rows_one[0]
    assert len(rows_two) == 61 - 50  # 60 changes + the first admin = 61 entries
    assert 'href="/settings/log?page=2"' in page_one
    assert 'href="/settings/log?page=1"' in page_two


@pytest.mark.parametrize("page", ["0", "-1", "999", "abc"])
def test_odd_page_numbers_do_not_break(client: AppClient, page: str) -> None:
    response = client.get(f"/settings/log?page={page}")

    assert response.status_code in {200, 422}
    assert "Traceback" not in response.text


# --- Test case 13: time zone -----------------------------------------------------------


def test_times_are_shown_in_the_configured_time_zone(
    client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed = datetime(2026, 7, 1, 10, 5, tzinfo=UTC).replace(tzinfo=None)
    monkeypatch.setattr(auth, "utcnow", lambda: fixed)
    client.post("/settings", data=valid_form(markup="45"))

    html = client.get("/settings/log").text

    # Summer time in Berlin: UTC + 2 hours.
    assert "01.07.2026 12:05" in log_rows(html)[0]


def test_other_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_TIMEZONE", "America/New_York")

    assert Settings.from_env().timezone == "America/New_York"


@pytest.mark.parametrize("value", ["Mars/Olympus", "../etc/passwd", "Europe/", "Europe", "UTC+2"])
def test_invalid_time_zone_stops_the_start(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAKERHUB_TIMEZONE", value)

    with pytest.raises(ConfigError) as error:
        create_app()

    assert "MAKERHUB_TIMEZONE" in str(error.value)


def test_time_zone_defaults_to_berlin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAKERHUB_TIMEZONE", raising=False)

    assert Settings.from_env().timezone == "Europe/Berlin"


def test_time_zone_is_explained_in_env_example() -> None:
    example = (APP_DIR.parent / ".env.example").read_text(encoding="utf-8")

    assert "# MAKERHUB_TIMEZONE=Europe/Berlin" in example


# --- Test case 14: no way to change or delete ------------------------------------------


def test_log_pages_only_read() -> None:
    app = create_app()
    log_routes = [
        route for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/settings/log")
    ]  # fmt: skip

    assert log_routes
    assert all(route.methods == {"GET"} for route in log_routes)


def test_log_pages_have_no_forms_except_logout(client: AppClient) -> None:
    for path in ("/settings/log", "/settings/log/logins"):
        forms = re.findall(r'<form\b[^>]*action="([^"]+)"', client.get(path).text)
        assert forms == ["/logout"]


# --- Test case 15: escaping ------------------------------------------------------------


def test_logged_values_are_escaped(client: AppClient) -> None:
    client.post("/settings", data=valid_form(spool_category="<b>fett</b>"))
    client.post(
        "/settings/users/new",
        data={
            "username": "<i>name</i>",
            "role": "employee",
            "password": OTHER_PASSWORD,
            "password_repeat": OTHER_PASSWORD,
        },
    )

    html = client.get("/settings/log").text

    assert "<b>fett</b>" not in html
    assert "<i>name</i>" not in html
    assert "&lt;b&gt;fett&lt;/b&gt;" in html
