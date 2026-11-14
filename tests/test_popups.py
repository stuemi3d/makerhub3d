"""Edit articles in pop-ups (issue #76, test cases 1-14). All data is invented.

The test case numbers refer to the comment on the issue. The pop-up itself (opening, closing,
focus, phone) is checked in tests/browser/test_popups.py; here the server side is checked.
"""

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.main import create_app
from app.models import Article, ChangeLogEntry
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as, signed_in_client

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
HX = {"HX-Request": "true"}


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


def add_article(number: str = "3D-0001", name: str = "Kabelhalter") -> int:
    with db() as session, session.begin():
        article = Article(
            number=number,
            number_key=number.casefold(),
            name=name,
            active=True,
            created_at=datetime(2026, 1, 1),
        )
        session.add(article)
        session.flush()
        return article.id


def stored_names() -> list[str]:
    with db() as session:
        return [a.name for a in session.query(Article).order_by(Article.id)]


def logged() -> list[tuple[str, str]]:
    with db() as session:
        return [(e.action, e.subject) for e in session.query(ChangeLogEntry).order_by("id")]


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


def is_fragment(html: str) -> bool:
    return "<html" not in html and "<header" not in html and "<form" in html


# --- Open (cases 1, 3, 5, 6) ------------------------------------------------------------------


def test_list_links_open_the_pop_up_and_keep_a_normal_address(client: AppClient) -> None:
    article_id = add_article()

    html = client.get("/articles").text

    edit = re.search(rf'<a [^>]*href="/articles/{article_id}/edit"[^>]*>', html)
    assert edit
    assert f'hx-get="/articles/{article_id}/edit"' in edit.group(0)
    assert "data-popup" in edit.group(0)
    new = re.search(r'<a [^>]*href="/articles/new"[^>]*>', html)
    assert new
    assert 'hx-get="/articles/new"' in new.group(0)
    assert "data-popup" in new.group(0)
    assert '<dialog id="popup"' in html


def test_pop_up_content_is_only_the_form_and_the_page_is_complete(client: AppClient) -> None:
    article_id = add_article()
    for path in ("/articles/new", f"/articles/{article_id}/edit"):
        popup = client.get(path, headers=HX)
        page = client.get(path)

        assert popup.status_code == page.status_code == 200
        assert is_fragment(popup.text)
        assert "<html" in page.text
        assert "topbar" in page.text
        assert "HX-Request" in popup.headers["vary"]
        # The same form: the same fields; only the pop-up form sends itself with HTMX.
        for field in ("number", "name", "selling_price", "weight", "min_stock"):
            assert f'name="{field}"' in popup.text
            assert f'name="{field}"' in page.text
        assert "hx-post" in popup.text
        assert "hx-post" not in page.text


def test_pop_up_form_has_token_title_and_focus_target(client: AppClient) -> None:
    html = client.get("/articles/new", headers=HX).text

    assert re.search(r'name="csrf_token" value="[^"]+"', html)
    assert 'id="popup-title"' in html
    assert DE["article.new.title"] in html
    assert re.search(r"<input[^>]*\bautofocus\b", html)


def test_edit_pop_up_shows_the_stored_values(client: AppClient) -> None:
    article_id = add_article("3D-0007", "Clip")

    html = client.get(f"/articles/{article_id}/edit", headers=HX).text

    assert 'value="3D-0007"' in html
    assert 'value="Clip"' in html
    assert DE["article.edit.title"] in html


# --- Save (cases 2, 3) ------------------------------------------------------------------------


def test_saving_an_edit_from_the_pop_up_answers_without_a_page(client: AppClient) -> None:
    article_id = add_article("3D-0001", "Kabelhalter")

    response = client.post(
        f"/articles/{article_id}",
        data={"number": "3D-0001", "name": "Kabelclip", "selling_price": "9,90"},
        headers=HX,
    )

    assert response.status_code == 204
    assert response.content == b""
    assert json.loads(response.headers["hx-trigger"]) == {"popup-saved": {}}
    assert stored_names() == ["Kabelclip"]
    assert ("article.changed", "3D-0001") in logged()


def test_creating_from_the_pop_up_answers_without_a_page(client: AppClient) -> None:
    response = client.post("/articles/new", data={"number": "3D-0100", "name": "Neu"}, headers=HX)

    assert response.status_code == 204
    assert json.loads(response.headers["hx-trigger"]) == {"popup-saved": {}}
    assert stored_names() == ["Neu"]
    assert ("article.created", "3D-0100") in logged()
    assert "3D-0100" in client.get("/articles").text


# --- Errors (case 4) --------------------------------------------------------------------------


def test_errors_stay_in_the_pop_up_and_keep_the_input(client: AppClient) -> None:
    add_article("3D-0001", "Kabelhalter")

    empty = client.post("/articles/new", data={"number": "3D-0002", "name": ""}, headers=HX)
    taken = client.post("/articles/new", data={"number": "3d-0001", "name": "Doppelt"}, headers=HX)

    for response, kept in ((empty, "3D-0002"), (taken, "Doppelt")):
        assert response.status_code == 422
        assert is_fragment(response.text)
        assert 'aria-invalid="true"' in response.text
        assert kept in response.text
        assert "hx-post" in response.text
    assert DE["article.error.taken"] in taken.text
    assert stored_names() == ["Kabelhalter"]


def test_edit_errors_stay_in_the_pop_up_and_change_nothing(client: AppClient) -> None:
    article_id = add_article("3D-0001", "Kabelhalter")

    response = client.post(
        f"/articles/{article_id}",
        data={"number": "3D-0001", "name": "Neu", "selling_price": "abc"},
        headers=HX,
    )

    assert response.status_code == 422
    assert is_fragment(response.text)
    assert 'value="abc"' in response.text
    assert stored_names() == ["Kabelhalter"]


# --- Without JavaScript (case 5) --------------------------------------------------------------


def test_plain_pages_still_redirect_and_show_errors_on_the_page(client: AppClient) -> None:
    created = client.post(
        "/articles/new", data={"number": "3D-0100", "name": "Neu"}, follow_redirects=False
    )
    assert created.status_code == 303
    assert re.fullmatch(r"/articles/\d+", created.headers["location"])

    article_id = int(created.headers["location"].rsplit("/", 1)[1])
    saved = client.post(
        f"/articles/{article_id}",
        data={"number": "3D-0100", "name": "Neuer Name"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == f"/articles/{article_id}?saved=1"

    failed = client.post("/articles/new", data={"number": "3D-0101", "name": ""})
    assert failed.status_code == 422
    assert "<html" in failed.text
    assert 'aria-invalid="true"' in failed.text


# --- Security (cases 7-10) --------------------------------------------------------------------


def test_without_login_neither_page_nor_pop_up_is_shown() -> None:
    with AppClient(create_app()) as anonymous:
        for headers in ({}, HX):
            response = anonymous.get("/articles/new", headers=headers, follow_redirects=False)
            assert response.status_code in (303, 401, 403)
            assert "<form" not in response.text or "/login" in response.headers.get("location", "")
            assert 'name="number"' not in response.text


def test_reader_gets_no_pop_up_and_no_symbols(reader: AppClient, client: AppClient) -> None:
    article_id = add_article()

    for path in ("/articles/new", f"/articles/{article_id}/edit"):
        for headers in ({}, HX):
            response = reader.get(path, headers=headers)
            assert response.status_code == 403
            assert 'name="number"' not in response.text
    for headers in ({}, HX):
        response = reader.post(
            f"/articles/{article_id}", data={"number": "3D-0001", "name": "Neu"}, headers=headers
        )
        assert response.status_code == 403
    assert stored_names() == ["Kabelhalter"]
    html = reader.get("/articles").text
    assert "/edit" not in html
    assert "/articles/new" not in html
    assert "hx-get" not in html


def test_employee_may_use_the_pop_up(client: AppClient) -> None:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        assert employee.get("/articles/new", headers=HX).status_code == 200
        response = employee.post("/articles/new", data={"number": "3D-1", "name": "A"}, headers=HX)
        assert response.status_code == 204


def test_saving_from_the_pop_up_without_a_form_token_is_refused(client: AppClient) -> None:
    article_id = add_article()
    client.csrf_token = None

    response = client.post(
        f"/articles/{article_id}",
        data={"number": "3D-0001", "name": "Neu", "csrf_token": "falsch"},
        headers=HX,
    )

    assert response.status_code == 403
    assert stored_names() == ["Kabelhalter"]


def test_htmx_is_a_local_file_and_pages_load_nothing_from_other_servers(
    client: AppClient,
) -> None:
    static = APP_DIR / "static"
    assert (static / "htmx.min.js").is_file()
    assert (static / "htmx.LICENSE.md").is_file()
    served = client.get("/static/htmx.min.js")
    assert served.status_code == 200

    for path in ("/articles", "/articles/new"):
        html = client.get(path).text
        assert 'src="/static/htmx.min.js"' in html
        assert not re.findall(r'(?:src|href)="(?:https?:)?//', html)
    # HTMX may only talk to this server and runs no scripts from answers.
    script = client.get("/static/app.js").text
    assert "selfRequestsOnly: true" in script
    assert "allowEval: false" in script
    assert "allowScriptTags: false" in script


def test_list_has_a_region_that_is_refreshed_after_saving(client: AppClient) -> None:
    add_article()

    html = client.get("/articles").text

    assert re.search(r'<div id="articles-list"[^>]*data-popup-refresh', html)
