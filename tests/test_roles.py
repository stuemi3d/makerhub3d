"""Roles and permissions (issue #28, test cases 1-5)."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.main import create_app
from app.models import AppSettings, User
from app.permissions import ROLE_PERMISSIONS, Permission, requires, route_permission
from tests.auth import (
    EMPLOYEE_NAME,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
)
from tests.forms import valid_form

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
# Reachable without login (issue #8); they need no permission level.
# /login/code is the code step of a two-factor login (issue #30), part of the login.
WITHOUT_LOGIN = {"/login", "/login/code", "/setup", "/healthz"}
MANAGE_PAGES = ["/settings", "/settings/users", "/settings/users/new"]


def stored_markup() -> Decimal:
    with Session(make_engine(Settings.from_env().database_path)) as session:
        return session.get(AppSettings, 1).markup


def nav_of(html: str) -> str:
    return re.search(r"<nav\b.*?</nav>", html, re.DOTALL).group(0)


@pytest.fixture
def employee(client: AppClient) -> AppClient:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as test_client:
        yield test_client


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


# --- Test case 1: the admin may do everything ------------------------------------------


def test_roles_have_the_rights_from_the_plan() -> None:
    assert ROLE_PERMISSIONS["admin"] == {Permission.VIEW, Permission.EDIT, Permission.MANAGE}
    assert ROLE_PERMISSIONS["employee"] == {Permission.VIEW, Permission.EDIT}
    assert ROLE_PERMISSIONS["read_only"] == {Permission.VIEW}


@pytest.mark.parametrize("path", ["/", *MANAGE_PAGES])
def test_admin_reaches_every_page(client: AppClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_admin_sees_settings_in_the_menu(client: AppClient) -> None:
    assert 'href="/settings"' in nav_of(client.get("/").text)


# --- Test case 2: employee and read-only do not reach the settings ---------------------


@pytest.mark.parametrize("role_client", ["employee", "reader"])
def test_others_see_the_dashboard_but_not_the_settings_entry(
    request: pytest.FixtureRequest, role_client: str
) -> None:
    other = request.getfixturevalue(role_client)

    response = other.get("/")

    assert response.status_code == 200
    assert 'href="/settings"' not in nav_of(response.text)
    assert DE["nav.logout"] in response.text


@pytest.mark.parametrize("role_client", ["employee", "reader"])
@pytest.mark.parametrize("path", MANAGE_PAGES)
def test_others_get_no_permission_page(
    request: pytest.FixtureRequest, role_client: str, path: str
) -> None:
    other = request.getfixturevalue(role_client)

    response = other.get(path)

    assert response.status_code == 403
    assert DE["error.no_permission"] in response.text
    # A normal page with menu, so the user can go on from there.
    assert 'href="/"' in nav_of(response.text)


@pytest.mark.parametrize("role_client", ["employee", "reader"])
def test_others_cannot_save_settings(request: pytest.FixtureRequest, role_client: str) -> None:
    other = request.getfixturevalue(role_client)

    response = other.post("/settings", data=valid_form(markup="99"))

    assert response.status_code == 403
    assert stored_markup() == Decimal("30")


@pytest.mark.parametrize("role_client", ["employee", "reader"])
def test_others_cannot_create_users(request: pytest.FixtureRequest, role_client: str) -> None:
    other = request.getfixturevalue(role_client)
    form = {
        "username": "eindringling",
        "role": "admin",
        "password": "ein-langes-passwort",
        "password_repeat": "ein-langes-passwort",
    }

    assert other.post("/settings/users/new", data=form).status_code == 403
    with Session(make_engine(Settings.from_env().database_path)) as session:
        assert session.query(User).filter_by(username="eindringling").count() == 0


# --- Test case 3: every page has a permission level ------------------------------------


def test_every_page_has_exactly_one_permission_level() -> None:
    app = create_app()
    missing = []
    for route in app.routes:
        is_page = isinstance(route, APIRoute) and route.path not in WITHOUT_LOGIN
        if is_page and route_permission(route) is None:
            missing.append(f"{sorted(route.methods)} {route.path}")

    assert missing == []


def test_permission_check_finds_a_page_without_level() -> None:
    app = create_app()

    @app.get("/forgotten")
    def forgotten() -> None:
        return None

    route = next(r for r in app.routes if getattr(r, "path", "") == "/forgotten")
    assert route_permission(route) is None


@pytest.mark.parametrize(
    ("path", "permission"),
    [("/", Permission.VIEW), ("/settings", Permission.MANAGE), ("/logout", Permission.VIEW)],
)
def test_known_pages_have_the_agreed_level(path: str, permission: Permission) -> None:
    app = create_app()
    levels = {route_permission(r) for r in app.routes if getattr(r, "path", "") == path}

    assert levels == {permission}


# --- Test case 4: read-only cannot change anything, employees can ----------------------


def app_with_edit_page():
    """Real edit pages come with M1; until then this test page stands in for them."""
    app = create_app()

    @app.post("/test-edit", dependencies=[requires(Permission.EDIT)])
    async def test_edit(request: Request) -> PlainTextResponse:
        return PlainTextResponse("changed")

    return app


def test_reader_cannot_send_edit_forms(client: AppClient) -> None:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME, app=app_with_edit_page()) as reader:
        response = reader.post("/test-edit", data={})

    assert response.status_code == 403
    assert "changed" not in response.text


@pytest.mark.parametrize("username", [EMPLOYEE_NAME, "zweiter-admin"])
def test_employee_and_admin_can_send_edit_forms(client: AppClient, username: str) -> None:
    add_user(username, "employee" if username == EMPLOYEE_NAME else "admin")
    with client_as(username, app=app_with_edit_page()) as editor:
        response = editor.post("/test-edit", data={})

    assert response.status_code == 200
    assert response.text == "changed"


# --- Test case 5: a changed role works at once -----------------------------------------


def test_changed_role_works_without_new_login(client: AppClient) -> None:
    user_id = add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        assert employee.get("/settings").status_code == 403

        client.post(f"/settings/users/{user_id}", data={"role": "admin", "active": "1"})

        assert employee.get("/settings").status_code == 200
        client.post(f"/settings/users/{user_id}", data={"role": "read_only", "active": "1"})
        assert employee.get("/settings").status_code == 403


# --- Review: user-changing pages are admin only ----------------------------------------


def test_every_settings_page_needs_the_manage_level() -> None:
    app = create_app()
    settings_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/settings")
    ]

    assert len(settings_routes) >= 7
    for route in settings_routes:
        assert route_permission(route) == Permission.MANAGE, route.path


@pytest.mark.parametrize("role", ["employee", "read_only"])
def test_others_cannot_change_users_or_themselves(client: AppClient, role: str) -> None:
    own_id = add_user(EMPLOYEE_NAME, role)
    with client_as(EMPLOYEE_NAME) as other:
        for target in (own_id, 1):
            assert other.get(f"/settings/users/{target}").status_code == 403
            response = other.post(
                f"/settings/users/{target}", data={"role": "admin", "active": "1"}
            )
            assert response.status_code == 403
            response = other.post(
                f"/settings/users/{target}/password",
                data={
                    "password": "neues-langes-passwort",
                    "password_repeat": "neues-langes-passwort",
                },
            )
            assert response.status_code == 403

    with Session(make_engine(Settings.from_env().database_path)) as session:
        assert session.get(User, own_id).role == role
