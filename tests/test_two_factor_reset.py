"""Resetting two-factor login: by an admin and by a command on the server (issue #39).

Test cases 1-16; test case 17 (phone view) is in tests/browser/test_two_factor_reset.py.
All keys, names and passwords here are invented.
"""

import json
import re
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app import auth, cli, two_factor
from app.config import Settings
from app.db import make_engine, run_migrations
from app.models import ChangeLogEntry, LoginSession, RecoveryCode, User
from app.two_factor import Cipher
from tests.auth import (
    ADMIN_NAME,
    EMPLOYEE_NAME,
    OTHER_PASSWORD,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
    csrf_from,
    log_in,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
REPO_DIR = APP_DIR.parent
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
# Invented key, built from repeated words so it does not look like a real secret.
TEST_KEY = "erfunden-" * 5
SECOND_NAME = "zweiter-drucker"
RESET_BUTTON = DE["user.two_factor.reset.submit"]


@pytest.fixture(autouse=True)
def two_factor_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_2FA_KEY", TEST_KEY)


def db() -> Session:
    engine = make_engine(Settings.from_env().database_path)
    run_migrations(engine)
    return Session(engine)


def user_named(username: str) -> User:
    with db() as session:
        return session.query(User).filter_by(username=username).one()


def switch_on(username: str) -> tuple[str, list[str]]:
    """Turns two-factor login on for a user directly in the database; returns the secret
    and the recovery codes."""
    secret = two_factor.new_secret()
    codes = two_factor.new_recovery_codes()
    with db() as session, session.begin():
        user = session.query(User).filter_by(username=username).one()
        user.totp_secret = Cipher(TEST_KEY).encrypt(secret)
        session.add_all(
            RecoveryCode(user_id=user.id, code_hash=two_factor.recovery_code_hash(code))
            for code in codes
        )
    return secret, codes


def is_on(username: str) -> bool:
    return user_named(username).totp_secret is not None


def recovery_code_count(username: str) -> int:
    user_id = user_named(username).id
    with db() as session:
        return session.query(RecoveryCode).filter_by(user_id=user_id).count()


def changes() -> list[ChangeLogEntry]:
    with db() as session:
        return list(session.query(ChangeLogEntry).order_by(ChangeLogEntry.id))


def password_step(app, username: str = EMPLOYEE_NAME, password: str = OTHER_PASSWORD) -> str:
    """Logs in with the password in a fresh browser; returns where the app sends it next."""
    with AppClient(app) as visitor:
        response = log_in(visitor, username, password, follow_redirects=False)
        return response.headers["location"]


@pytest.fixture
def employee(client: AppClient) -> tuple[int, str, list[str]]:
    """An employee with two-factor login switched on: id, secret, recovery codes."""
    user_id = add_user(EMPLOYEE_NAME, "employee")
    secret, codes = switch_on(EMPLOYEE_NAME)
    return user_id, secret, codes


def reset(client: AppClient, user_id: int, **kwargs):
    return client.post(f"/settings/users/{user_id}/2fa-reset", data={}, **kwargs)


# --- Test cases 1-2: status and button on the user page --------------------------------


def test_user_page_shows_two_factor_on_with_reset_button(client: AppClient, employee) -> None:
    page = client.get(f"/settings/users/{employee[0]}").text

    assert DE["user.two_factor.title"] in page
    assert DE["user.two_factor.status.on"] in page
    assert RESET_BUTTON in page
    assert f'action="/settings/users/{employee[0]}/2fa-reset"' in page


def test_user_page_shows_two_factor_off_without_button(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")

    page = client.get(f"/settings/users/{user_id}").text

    assert DE["user.two_factor.status.off"] in page
    assert RESET_BUTTON not in page


def test_reset_button_asks_for_confirmation(client: AppClient, employee) -> None:
    page = client.get(f"/settings/users/{employee[0]}").text

    assert f'data-confirm="{DE["user.two_factor.reset.confirm"]}"' in page
    assert '<script src="/static/app.js" defer></script>' in page


# --- Test cases 3-5: what resetting does -----------------------------------------------


def test_after_reset_the_password_is_enough(client: AppClient, employee) -> None:
    assert password_step(client.app).startswith("/login/code")

    response = reset(client, employee[0], follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/settings/users/{employee[0]}?tfa_reset=1"
    assert DE["user.two_factor.reset.done"] in client.get(response.headers["location"]).text
    assert not is_on(EMPLOYEE_NAME)
    assert password_step(client.app) == "/"


def test_after_reset_the_recovery_codes_are_gone(client: AppClient, employee) -> None:
    reset(client, employee[0])

    assert recovery_code_count(EMPLOYEE_NAME) == 0
    with db() as session:
        user = session.query(User).filter_by(username=EMPLOYEE_NAME).one()
        assert (user.totp_pending_secret, user.totp_last_step) == (None, None)


def test_reset_logs_the_user_out_everywhere(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME, app=client.app) as employee_client:
        switch_on(EMPLOYEE_NAME)
        with AppClient(client.app) as half_way:
            log_in(half_way, EMPLOYEE_NAME, OTHER_PASSWORD, follow_redirects=False)

            reset(client, user_id)

            assert employee_client.get("/", follow_redirects=False).status_code == 303
            # The started code step does not work any more either.
            assert half_way.get("/login/code", follow_redirects=False).status_code == 303
    with db() as session:
        assert session.query(LoginSession).filter_by(user_id=user_id).count() == 0
    # The admin stays logged in.
    assert client.get("/", follow_redirects=False).status_code == 200


def test_user_can_set_it_up_again(client: AppClient, employee) -> None:
    reset(client, employee[0])

    with client_as(EMPLOYEE_NAME, app=client.app) as employee_client:
        page = employee_client.post("/account/2fa/start", data={"tfa_password": OTHER_PASSWORD})
        assert page.status_code == 200
        secret = re.search(r'<code class="secret">([A-Z2-7 ]+)</code>', page.text).group(1)
        code = two_factor.code_for_step(
            secret.replace(" ", ""), two_factor.time_step(auth.utcnow())
        )
        done = employee_client.post("/account/2fa/confirm", data={"code": code})
        assert done.status_code == 200
    assert is_on(EMPLOYEE_NAME)


def test_reset_when_already_off_changes_nothing(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME, app=client.app) as employee_client:
        before = len(changes())

        response = reset(client, user_id, follow_redirects=False)

        assert response.status_code == 303
        assert len(changes()) == before
        assert employee_client.get("/", follow_redirects=False).status_code == 200


# --- Test case 6: not for the own account ----------------------------------------------


def test_own_page_has_no_reset_button(client: AppClient) -> None:
    switch_on(ADMIN_NAME)
    admin_id = user_named(ADMIN_NAME).id

    page = client.get(f"/settings/users/{admin_id}").text

    assert DE["user.two_factor.status.on"] in page
    assert RESET_BUTTON not in page
    assert DE["user.two_factor.self"] in page


def test_own_reset_sent_directly_is_rejected(client: AppClient) -> None:
    switch_on(ADMIN_NAME)
    admin_id = user_named(ADMIN_NAME).id

    response = reset(client, admin_id)

    assert response.status_code == 422
    assert DE["user.error.self_2fa"] in response.text
    assert is_on(ADMIN_NAME)


# --- Test cases 7-9: rights, form token, unknown user ----------------------------------


@pytest.mark.parametrize(("name", "role"), [(SECOND_NAME, "employee"), (READER_NAME, "read_only")])
def test_others_cannot_reset(client: AppClient, employee, name: str, role: str) -> None:
    add_user(name, role)
    with client_as(name, app=client.app) as other:
        assert other.get(f"/settings/users/{employee[0]}").status_code == 403
        assert reset(other, employee[0]).status_code == 403
    assert is_on(EMPLOYEE_NAME)


def test_logged_out_visitor_is_sent_to_login(client: AppClient, employee) -> None:
    with AppClient(client.app) as visitor:
        page = visitor.get("/login")
        response = visitor.post(
            f"/settings/users/{employee[0]}/2fa-reset",
            data={"csrf_token": csrf_from(page.text)},
            follow_redirects=False,
        )
    assert response.status_code in (303, 403)
    assert is_on(EMPLOYEE_NAME)


def test_reset_form_carries_the_token(client: AppClient, employee) -> None:
    page = client.get(f"/settings/users/{employee[0]}").text
    form = re.search(r"<form[^>]*2fa-reset.*?</form>", page, re.DOTALL).group(0)

    assert f'name="csrf_token" value="{client.csrf_token}"' in form


def test_reset_without_token_is_rejected(client: AppClient, employee) -> None:
    response = client.post(
        f"/settings/users/{employee[0]}/2fa-reset", data={"csrf_token": "falsch"}
    )

    assert response.status_code == 403
    assert is_on(EMPLOYEE_NAME)


def test_unknown_user_is_not_found(client: AppClient) -> None:
    assert reset(client, 9999).status_code == 404


# --- Test cases 10-14: command on the server -------------------------------------------


@pytest.mark.parametrize("typed", [EMPLOYEE_NAME, EMPLOYEE_NAME.upper()])
def test_command_resets_only_the_named_user(client: AppClient, employee, capsys, typed) -> None:
    switch_on(ADMIN_NAME)

    assert cli.main(["reset-2fa", typed]) == 0

    assert not is_on(EMPLOYEE_NAME)
    assert recovery_code_count(EMPLOYEE_NAME) == 0
    assert is_on(ADMIN_NAME)
    assert recovery_code_count(ADMIN_NAME) == 10
    output = capsys.readouterr()
    assert DE["cli.reset_2fa.user_done"] in output.out
    # Names are personal data and do not go into the terminal output.
    assert EMPLOYEE_NAME.lower() not in (output.out + output.err).lower()
    assert password_step(client.app) == "/"


def test_command_logs_the_named_user_out(client: AppClient) -> None:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME, app=client.app) as employee_client:
        switch_on(EMPLOYEE_NAME)

        cli.main(["reset-2fa", EMPLOYEE_NAME])

        assert employee_client.get("/", follow_redirects=False).status_code == 303


def test_command_with_unknown_name_changes_nothing(client: AppClient, employee, capsys) -> None:
    before = len(changes())

    assert cli.main(["reset-2fa", "gibt-es-nicht"]) == 1

    assert DE["cli.reset_2fa.unknown_user"] in (capsys.readouterr().err)
    assert is_on(EMPLOYEE_NAME)
    assert len(changes()) == before


def test_command_for_a_user_without_two_factor_changes_nothing(client: AppClient, capsys) -> None:
    add_user(EMPLOYEE_NAME, "employee")
    before = len(changes())

    assert cli.main(["reset-2fa", EMPLOYEE_NAME]) == 0

    expected = DE["cli.reset_2fa.user_was_off"]
    assert expected in capsys.readouterr().out
    assert len(changes()) == before


def test_command_without_name_still_resets_everybody(client: AppClient, employee) -> None:
    switch_on(ADMIN_NAME)

    assert cli.main(["reset-2fa"]) == 0

    assert not is_on(EMPLOYEE_NAME)
    assert not is_on(ADMIN_NAME)
    assert changes()[-1].action == "system.2fa_reset_all"


def test_command_with_too_many_words_shows_usage(client: AppClient, employee, capsys) -> None:
    assert cli.main(["reset-2fa", EMPLOYEE_NAME, "noch-was"]) == 2

    assert DE["cli.usage"] in capsys.readouterr().err
    assert is_on(EMPLOYEE_NAME)


def test_guide_explains_lost_phone() -> None:
    guide = (REPO_DIR / "docs" / "erster-start.md").read_text(encoding="utf-8")

    assert "Handy verloren" in guide
    assert "reset-2fa <Benutzername>" in guide


# --- Test case 15: user list -----------------------------------------------------------


def test_user_list_shows_two_factor_on_or_off(client: AppClient, employee) -> None:
    add_user(SECOND_NAME, "employee")

    page = client.get("/settings/users").text

    assert DE["users.col.two_factor"] in page
    rows = {
        name: re.search(rf">{re.escape(name)}</a></td>(.*?)</tr>", page, re.DOTALL).group(1)
        for name in (EMPLOYEE_NAME, SECOND_NAME)
    }
    assert DE["user.two_factor.on"] in rows[EMPLOYEE_NAME]
    assert DE["user.two_factor.off"] in rows[SECOND_NAME]


# --- Test case 16: change log ----------------------------------------------------------


def test_admin_reset_is_logged(client: AppClient, employee) -> None:
    reset(client, employee[0])

    entry = changes()[-1]
    assert (entry.action, entry.user_id, entry.subject) == (
        "user.2fa_reset",
        user_named(ADMIN_NAME).id,
        EMPLOYEE_NAME,
    )
    assert DE["log.action.user.2fa_reset"] in client.get("/settings/log").text


def test_command_reset_is_logged_as_server_command(client: AppClient, employee) -> None:
    cli.main(["reset-2fa", EMPLOYEE_NAME])

    entry = changes()[-1]
    assert (entry.action, entry.user_id, entry.subject) == ("user.2fa_reset", None, EMPLOYEE_NAME)
    page = client.get("/settings/log").text
    assert DE["log.action.user.2fa_reset"] in page
    assert DE["log.server_command"] in page


def test_no_secret_or_code_in_the_log(client: AppClient, employee) -> None:
    _, secret, codes = employee
    reset(client, employee[0])

    with db() as session:
        logged = " ".join(
            f"{e.action} {e.subject} {e.details}" for e in session.query(ChangeLogEntry)
        )
    for value in (secret, TEST_KEY, *codes):
        assert value not in logged
