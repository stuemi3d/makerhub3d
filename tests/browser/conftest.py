"""Browser tests: a real app on 127.0.0.1 with its own empty database, driven by Chromium.

Run with `uv run pytest -m browser`. The browser may only talk to the test app; every other
request is blocked and fails the test. Screenshots of failed tests go to `test-results/`.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import BrowserContext, Page, Route
from sqlalchemy.orm import Session

from app import auth
from app.config import DATABASE_FILE
from app.db import make_engine, run_migrations
from app.models import UserRole

REPO_DIR = Path(__file__).resolve().parents[2]
LOCALE_FILE = REPO_DIR / "app" / "locales" / "de.json"
HOST = "127.0.0.1"
ADMIN_NAME = "werkstatt"
ADMIN_PASSWORD = "ein-langes-test-passwort"
# Desktop and a narrow phone; every browser test runs in both.
VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "phone": {"width": 390, "height": 844},
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # Everything in this folder is a browser test, so `-m "not browser"` skips it all.
    for item in items:
        if Path(str(item.path)).is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.browser)


def t(key: str) -> str:
    """A German interface text, so tests do not repeat the wording."""
    return json.loads(LOCALE_FILE.read_text(encoding="utf-8"))[key]


@dataclass(frozen=True)
class LiveServer:
    url: str
    data_dir: Path

    @property
    def netloc(self) -> str:
        return urlsplit(self.url).netloc


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def _create_admin(data_dir: Path) -> None:
    engine = make_engine(data_dir / DATABASE_FILE)
    run_migrations(engine)
    with Session(engine) as db, db.begin():
        auth.create_user(db, ADMIN_NAME, ADMIN_PASSWORD, UserRole.ADMIN, must_change_password=False)
    engine.dispose()


def _wait_until_ready(url: str, process: subprocess.Popen, log_file: Path) -> None:
    for _ in range(100):
        if process.poll() is not None:
            break
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=1):  # noqa: S310 (127.0.0.1)
                return
        except OSError:
            time.sleep(0.1)
    process.kill()
    pytest.fail(f"Test app did not start:\n{log_file.read_text(encoding='utf-8')}")


@pytest.fixture(scope="session")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    """The app as a separate process on a free local port, with one admin in a temp folder."""
    data_dir = tmp_path_factory.mktemp("browser-app") / "data"
    data_dir.mkdir()
    _create_admin(data_dir)
    port = _free_port()
    url = f"http://{HOST}:{port}"
    # Only what the app needs; no settings from the developer's own environment.
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAKERHUB_")}
    env.update(MAKERHUB_DATA_DIR=str(data_dir), MAKERHUB_INSECURE_COOKIES="true")
    log_file = data_dir.parent / "app.log"
    with log_file.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(  # noqa: S603 (fixed arguments, no user input)
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(port)],
            cwd=REPO_DIR,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_until_ready(url, process, log_file)
            yield LiveServer(url=url, data_dir=data_dir)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


@pytest.fixture(params=list(VIEWPORTS))
def viewport_name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def browser_context_args(browser_context_args: dict, viewport_name: str) -> dict:
    args = {
        **browser_context_args,
        "viewport": VIEWPORTS[viewport_name],
        "locale": "de-DE",
        # Service workers would bypass the request filter below.
        "service_workers": "block",
    }
    if viewport_name == "phone":
        args.update(is_mobile=True, has_touch=True)
    return args


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    # Second lock besides the request filter below, also for WebSockets and raw IP addresses:
    # no name except 127.0.0.1 can be resolved, and everything else goes to a dead proxy.
    args = [
        f"--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE {HOST}",
        f"--proxy-server={HOST}:9",
        f"--proxy-bypass-list={HOST}",
    ]
    return {**browser_type_launch_args, "args": args}


@pytest.fixture(autouse=True)
def blocked_requests(context: BrowserContext, live_server: LiveServer) -> Iterator[list[str]]:
    """Blocks every request except those to the test app; a blocked request fails the test."""
    blocked: list[str] = []

    def only_local(route: Route) -> None:
        url = route.request.url
        if (
            url.startswith(("about:", "data:", "blob:"))
            or urlsplit(url).netloc == live_server.netloc
        ):
            route.continue_()
        else:
            blocked.append(url)
            route.abort()

    context.route("**/*", only_local)
    yield blocked
    assert not blocked, f"The browser tried to reach other addresses: {blocked}"


@pytest.fixture(scope="session")
def base_url(live_server: LiveServer) -> str:
    return live_server.url


@pytest.fixture
def login_page(page: Page, live_server: LiveServer) -> Page:
    page.goto(f"{live_server.url}/login")
    return page


@pytest.fixture
def admin_page(login_page: Page) -> Page:
    """The browser page, logged in as the admin through the real login form."""
    login_page.fill("#username", ADMIN_NAME)
    login_page.fill("#password", ADMIN_PASSWORD)
    login_page.click(f"button[type=submit]:has-text('{t('login.submit')}')")
    login_page.wait_for_url(lambda url: not url.endswith("/login"))
    return login_page
