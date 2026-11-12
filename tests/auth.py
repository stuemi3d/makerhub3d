"""Shared login helpers for tests. All names and passwords here are invented."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import auth
from app.config import Settings
from app.db import make_engine, run_migrations
from app.main import create_app
from app.models import UserRole

ADMIN_NAME = "werkstatt"
ADMIN_PASSWORD = "ein-langes-test-passwort"
# Secure cookies are only sent over HTTPS, as behind the reverse proxy.
BASE_URL = "https://testserver"


def csrf_from(html: str) -> str | None:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else None


class AppClient(TestClient):
    """Test client over HTTPS that adds the form token (CSRF) to every POST once known."""

    def __init__(self, app: FastAPI, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", BASE_URL)
        super().__init__(app, **kwargs)
        self.csrf_token: str | None = None

    def post(self, url: Any, *, data: Any = None, **kwargs: Any) -> Any:
        if isinstance(data, dict) and "csrf_token" not in data and self.csrf_token:
            data = {**data, "csrf_token": self.csrf_token}
        return super().post(url, data=data, **kwargs)


def setup_form(client: AppClient, **overrides: str) -> dict[str, str]:
    """A valid form for the setup page, with the form token from the page itself."""
    form = {
        "csrf_token": csrf_from(client.get("/setup").text)
        or getattr(client, "csrf_token", "")
        or "",
        "setup_code": client.app.state.setup_code or "",
        "username": ADMIN_NAME,
        "password": ADMIN_PASSWORD,
        "password_repeat": ADMIN_PASSWORD,
    }
    form.update(overrides)
    return form


def log_in(
    client: TestClient,
    username: str = ADMIN_NAME,
    password: str = ADMIN_PASSWORD,
    next_url: str | None = None,
    **kwargs: Any,
) -> Any:
    page = client.get("/login")
    form = {"csrf_token": csrf_from(page.text) or "", "username": username, "password": password}
    if next_url is not None:
        form["next"] = next_url
    return client.post("/login", data=form, **kwargs)


def sign_in(client: AppClient) -> None:
    """Creates the first admin (or logs in if one exists) and remembers the form token."""
    if client.app.state.setup_code:
        response = client.post("/setup", data=setup_form(client))
    else:
        response = log_in(client)
    assert response.status_code == 200, response.text
    client.csrf_token = csrf_from(client.get("/").text)
    assert client.csrf_token


@contextmanager
def signed_in_client(app: FastAPI | None = None, **kwargs: Any) -> Iterator[AppClient]:
    with AppClient(app or create_app(), **kwargs) as client:
        sign_in(client)
        yield client


EMPLOYEE_NAME = "mitarbeiterin"
READER_NAME = "leser"
OTHER_PASSWORD = "noch-ein-test-passwort"


def add_user(
    username: str, role: str, password: str = OTHER_PASSWORD, must_change_password: bool = False
) -> int:
    """Creates a user directly in the database of the current test and returns its id.

    By default without the duty to change the password, so tests can use the app at once.
    """
    engine = make_engine(Settings.from_env().database_path)
    run_migrations(engine)
    with Session(engine) as db, db.begin():
        user = auth.create_user(
            db, username, password, UserRole(role), must_change_password=must_change_password
        )
        return user.id


@contextmanager
def client_as(
    username: str, password: str = OTHER_PASSWORD, app: FastAPI | None = None
) -> Iterator[AppClient]:
    """A client logged in as an existing user."""
    with AppClient(app or create_app()) as client:
        response = log_in(client, username, password)
        assert response.status_code == 200, response.text
        client.csrf_token = csrf_from(client.get("/").text)
        yield client
