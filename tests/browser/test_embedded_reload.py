"""Embedded in another page (iframe): a reload keeps the page (issue #152, test cases 1-9).

The host page imitates Bambuddy: another address (own port) that shows MakerHub in an iframe.
Everything runs on 127.0.0.1; the browser may not reach any other address.
"""

import socket
from collections.abc import Iterator

import pytest
from playwright.sync_api import BrowserContext, Frame, Page, expect

from tests.browser.conftest import (
    ADMIN_NAME,
    ADMIN_PASSWORD,
    HOST,
    LiveServer,
    _run_app,
    t,
)

STORE_KEY = "makerhub.lastPage"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def host_origin() -> str:
    return f"http://{HOST}:{_free_port()}"


@pytest.fixture(scope="module")
def embed_server(
    tmp_path_factory: pytest.TempPathFactory, host_origin: str
) -> Iterator[LiveServer]:
    """The app, which may be embedded by the host page's address."""
    with _run_app(tmp_path_factory, "browser-embed", {"MAKERHUB_EMBED_ORIGINS": host_origin}) as s:
        yield s


@pytest.fixture
def blocked_requests() -> list[str]:
    # The shared filter only knows the normal test app; this module sets up its own (below).
    return []


@pytest.fixture(autouse=True)
def only_local_addresses(
    context: BrowserContext, embed_server: LiveServer, host_origin: str
) -> Iterator[None]:
    blocked: list[str] = []

    def handle(route) -> None:
        url = route.request.url
        if url.startswith(("about:", "data:", "blob:")):
            route.continue_()
        elif url == f"{host_origin}/host":
            route.fulfill(
                content_type="text/html",
                body=f'<!doctype html><iframe id="app" src="{embed_server.url}/" '
                'style="width:100%;height:700px"></iframe>',
            )
        elif route.request.url.split("/")[2] == embed_server.netloc:
            route.continue_()
        else:
            blocked.append(url)
            route.abort()

    context.route("**/*", handle)
    yield
    assert not blocked, f"The browser tried to reach other addresses: {blocked}"


def sign_in(page: Page, server: LiveServer) -> None:
    page.goto(f"{server.url}/login")
    page.fill("#username", ADMIN_NAME)
    page.fill("#password", ADMIN_PASSWORD)
    page.click(f"button[type=submit]:has-text('{t('login.submit')}')")
    page.wait_for_url(lambda url: not url.endswith("/login"))


def app_frame(page: Page, server: LiveServer, path: str | None = None) -> Frame:
    """The embedded app's frame once it has loaded (on `path`, when given)."""
    for _ in range(100):
        for frame in page.frames:
            if frame is page.main_frame or not frame.url.startswith(server.url):
                continue
            if path is None or frame.url == f"{server.url}{path}":
                frame.wait_for_load_state("load")
                return frame
        page.wait_for_timeout(100)
    pytest.fail(f"The embedded app did not show {path or 'a page'}: {[f.url for f in page.frames]}")


def go_in_frame(page: Page, server: LiveServer, path: str) -> Frame:
    """Goes to another page inside the embedded app, like a click on a link would."""
    frame = app_frame(page, server)
    frame.evaluate("(path) => window.location.assign(path)", path)
    return app_frame(page, server, path)


def open_embedded(page: Page, server: LiveServer, host_origin: str) -> Frame:
    sign_in(page, server)
    page.goto(f"{host_origin}/host")
    return app_frame(page, server)


def frame_path(page: Page, server: LiveServer, path: str) -> str:
    """The embedded app's current address (waits until it is `path`)."""
    return app_frame(page, server, path).url.removeprefix(server.url)


def reload_host(page: Page) -> None:
    page.reload()


def test_reload_keeps_the_page(page: Page, embed_server: LiveServer, host_origin: str) -> None:
    open_embedded(page, embed_server, host_origin)
    go_in_frame(page, embed_server, "/articles")
    reload_host(page)
    assert frame_path(page, embed_server, "/articles") == "/articles"


def test_reload_keeps_filter_and_sorting_in_the_address(
    page: Page, embed_server: LiveServer, host_origin: str
) -> None:
    open_embedded(page, embed_server, host_origin)
    go_in_frame(page, embed_server, "/articles")
    go_in_frame(page, embed_server, "/stock")
    go_in_frame(page, embed_server, "/articles?sort=name&dir=desc")
    reload_host(page)
    assert (
        frame_path(page, embed_server, "/articles?sort=name&dir=desc")
        == "/articles?sort=name&dir=desc"
    )


def test_a_new_tab_starts_on_the_dashboard(
    context: BrowserContext, page: Page, embed_server: LiveServer, host_origin: str
) -> None:
    open_embedded(page, embed_server, host_origin)
    go_in_frame(page, embed_server, "/articles")
    # A new tab (like opening Bambuddy again later) has its own, empty session storage.
    other = context.new_page()
    other.goto(f"{host_origin}/host")
    assert frame_path(other, embed_server, "/") == "/"


def test_not_embedded_nothing_is_remembered(page: Page, embed_server: LiveServer) -> None:
    sign_in(page, embed_server)
    page.goto(f"{embed_server.url}/articles")
    page.reload()
    assert page.url == f"{embed_server.url}/articles"
    assert page.evaluate("(key) => sessionStorage.getItem(key)", STORE_KEY) is None


def test_a_page_that_does_not_exist_anymore_does_not_trap_the_reload(
    page: Page, embed_server: LiveServer, host_origin: str
) -> None:
    frame = open_embedded(page, embed_server, host_origin)
    # The remembered article was deleted in the meantime: its answer is a bare error page
    # that has no script, so it can not forget itself.
    frame.evaluate(
        "([key, value]) => sessionStorage.setItem(key, value)", [STORE_KEY, "/articles/999999"]
    )
    reload_host(page)
    assert frame_path(page, embed_server, "/articles/999999") == "/articles/999999"
    reload_host(page)
    assert frame_path(page, embed_server, "/") == "/"
    reload_host(page)
    assert frame_path(page, embed_server, "/") == "/"


@pytest.mark.parametrize(
    "bad", ["//other.example/x", "https://other.example/x", "javascript:alert(1)", "articles"]
)
def test_foreign_addresses_in_the_storage_are_ignored(
    page: Page, embed_server: LiveServer, host_origin: str, bad: str
) -> None:
    frame = open_embedded(page, embed_server, host_origin)
    frame.evaluate("([key, value]) => sessionStorage.setItem(key, value)", [STORE_KEY, bad])
    reload_host(page)
    assert frame_path(page, embed_server, "/") == "/"


def test_an_open_popup_is_not_remembered(
    page: Page, embed_server: LiveServer, host_origin: str
) -> None:
    open_embedded(page, embed_server, host_origin)
    frame = go_in_frame(page, embed_server, "/articles")
    frame.get_by_role("button", name=t("articles.new"), exact=True).click()
    expect(frame.locator("dialog#popup[open]")).to_be_visible()
    reload_host(page)
    assert frame_path(page, embed_server, "/articles") == "/articles"
    frame = app_frame(page, embed_server)
    expect(frame.locator("dialog#popup[open]")).to_have_count(0)


def test_logging_out_forgets_the_page(
    page: Page, embed_server: LiveServer, host_origin: str
) -> None:
    open_embedded(page, embed_server, host_origin)
    frame = go_in_frame(page, embed_server, "/articles")
    frame.evaluate("() => document.querySelector('form.logout').requestSubmit()")
    frame = app_frame(page, embed_server, "/login")
    assert frame.evaluate("(key) => sessionStorage.getItem(key)", STORE_KEY) is None
    frame.fill("#username", ADMIN_NAME)
    frame.fill("#password", ADMIN_PASSWORD)
    frame.click(f"button[type=submit]:has-text('{t('login.submit')}')")
    app_frame(page, embed_server, "/")
    reload_host(page)
    assert frame_path(page, embed_server, "/") == "/"


def test_the_brand_link_leads_to_the_dashboard_and_stays_there(
    page: Page, embed_server: LiveServer, host_origin: str
) -> None:
    open_embedded(page, embed_server, host_origin)
    frame = go_in_frame(page, embed_server, "/articles")
    frame.locator("a.brand").click()
    assert frame_path(page, embed_server, "/") == "/"
    page.wait_for_timeout(500)
    assert frame_path(page, embed_server, "/") == "/"


def test_the_back_button_returns_to_the_dashboard(
    page: Page, embed_server: LiveServer, host_origin: str
) -> None:
    open_embedded(page, embed_server, host_origin)
    go_in_frame(page, embed_server, "/articles")
    frame = app_frame(page, embed_server, "/articles")
    frame.evaluate("() => window.history.back()")
    assert frame_path(page, embed_server, "/") == "/"
    page.wait_for_timeout(500)
    assert frame_path(page, embed_server, "/") == "/"
