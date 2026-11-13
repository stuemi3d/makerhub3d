"""Articles and the article detail page (issue #10, test cases 1-14). All data is invented."""

import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import Article, ChangeLogEntry
from tests.auth import (
    EMPLOYEE_NAME,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
    signed_in_client,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
SECTIONS = ["calculations", "productions", "stock", "sales", "analysis"]


def db() -> Session:
    return Session(make_engine(Settings.from_env().database_path))


def articles() -> list[Article]:
    with db() as session:
        return list(session.query(Article).order_by(Article.id))


def article_form(**overrides: str) -> dict[str, str]:
    form = {
        "number": "3D-0001",
        "name": "Kabelhalter",
        "selling_price": "",
        "weight": "",
        "min_stock": "",
    }
    form.update(overrides)
    return form


def create(client: AppClient, **overrides: str):
    return client.post("/articles/new", data=article_form(**overrides), follow_redirects=False)


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


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


# --- Test case 1: create an article -----------------------------------------------------


def test_new_article_appears_in_the_list(client: AppClient) -> None:
    response = create(client)

    assert response.status_code == 303
    stored = articles()
    assert [(a.number, a.name, a.active) for a in stored] == [("3D-0001", "Kabelhalter", True)]
    assert response.headers["location"] == f"/articles/{stored[0].id}"
    html = client.get("/articles").text
    assert "3D-0001" in html
    assert "Kabelhalter" in html


def test_navigation_links_to_the_article_list(client: AppClient) -> None:
    html = client.get("/").text

    assert re.search(r'<a href="/articles"[^>]*>' + DE["nav.articles"], html)
    assert client.get("/articles").status_code == 200


def test_empty_list_says_so(client: AppClient) -> None:
    assert DE["articles.empty"] in client.get("/articles").text


# --- Test case 2: article number is unique ----------------------------------------------


@pytest.mark.parametrize("second", ["3D-0001", "3d-0001", " 3D-0001 "])
def test_duplicate_number_is_refused(client: AppClient, second: str) -> None:
    create(client)

    response = create(client, number=second, name="Anderer Name")

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["article.error.taken"]
    assert [a.name for a in articles()] == ["Kabelhalter"]


def test_number_stays_as_entered_but_is_trimmed(client: AppClient) -> None:
    create(client, number="  3d-0042-rt ")

    assert [a.number for a in articles()] == ["3d-0042-rt"]


# --- Test case 3: required fields -------------------------------------------------------


@pytest.mark.parametrize("name", ["number", "name"])
@pytest.mark.parametrize("value", ["", "   "])
def test_number_and_name_are_required(client: AppClient, name: str, value: str) -> None:
    response = create(client, **{name: value})

    assert response.status_code == 422
    assert field_error(response.text, name) == DE["article.error.required"]
    assert articles() == []


def test_too_long_values_are_refused(client: AppClient) -> None:
    response = create(client, number="N" * 51, name="B" * 101)

    assert response.status_code == 422
    assert field_error(response.text, "number") == DE["article.error.too_long"]
    assert field_error(response.text, "name") == DE["article.error.too_long"]


def test_refused_form_keeps_the_entered_values(client: AppClient) -> None:
    response = create(client, name="", selling_price="12,90")

    assert 'value="3D-0001"' in response.text
    assert 'value="12,90"' in response.text


# --- Test case 4: optional numbers -------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "stored"), [("12,90", "12.90"), ("12.9", "12.9"), ("0", "0"), ("", None)]
)
def test_price_accepts_comma_and_dot(client: AppClient, price: str, stored: str | None) -> None:
    create(client, selling_price=price)

    value = articles()[0].selling_price
    assert (None if value is None else str(value)) == stored


def test_values_are_stored_as_exact_decimals(client: AppClient) -> None:
    create(client, selling_price="12,90", weight="2,5", min_stock="4")

    article = articles()[0]
    assert article.selling_price == Decimal("12.90")
    assert article.weight == Decimal("2.5")
    assert article.min_stock == 4


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("selling_price", "-1", "article.error.negative"),
        ("selling_price", "-0", "article.error.negative"),
        ("selling_price", "12,999", "article.error.decimals"),
        ("selling_price", "abc", "article.error.number"),
        ("selling_price", "1e3", "article.error.number"),
        ("selling_price", "99999999", "article.error.too_large"),
        ("weight", "-2", "article.error.negative"),
        ("weight", "0", "article.error.zero"),
        ("weight", "2,555", "article.error.decimals"),
        ("min_stock", "-1", "article.error.negative"),
        ("min_stock", "2,5", "article.error.whole"),
        ("min_stock", "abc", "article.error.number"),
        ("min_stock", "99999999", "article.error.too_large"),
    ],
)
def test_wrong_numbers_are_refused(client: AppClient, field: str, value: str, error: str) -> None:
    response = create(client, **{field: value})

    assert response.status_code == 422
    assert field_error(response.text, field) == DE[error]
    assert articles() == []


# --- Test case 5: edit, deactivate, reactivate, never delete -----------------------------


def test_article_can_be_edited(client: AppClient) -> None:
    create(client, selling_price="10")
    article_id = articles()[0].id

    page = client.get(f"/articles/{article_id}/edit")
    assert page.status_code == 200
    assert 'value="Kabelhalter"' in page.text
    response = client.post(
        f"/articles/{article_id}",
        data=article_form(name="Kabelhalter groß", selling_price="11,50", min_stock="3"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    article = articles()[0]
    assert (article.name, article.selling_price, article.min_stock) == (
        "Kabelhalter groß",
        Decimal("11.50"),
        3,
    )


def test_editing_cannot_reuse_another_number_but_may_keep_its_own(client: AppClient) -> None:
    create(client)
    create(client, number="3D-0002", name="Clip")
    first, second = (a.id for a in articles())

    taken = client.post(
        f"/articles/{second}", data=article_form(number="3d-0001"), follow_redirects=False
    )
    own = client.post(
        f"/articles/{second}",
        data=article_form(number="3D-0002", name="Clip neu"),
        follow_redirects=False,
    )

    assert taken.status_code == 422
    assert field_error(taken.text, "number") == DE["article.error.taken"]
    assert own.status_code == 303
    assert [a.name for a in articles() if a.id != first] == ["Clip neu"]


def test_unknown_article_gives_404(client: AppClient) -> None:
    for path in ("/articles/999", "/articles/999/edit", "/articles/999/stock"):
        assert client.get(path).status_code == 404
    assert client.post("/articles/999", data=article_form()).status_code == 404
    assert client.post("/articles/999/deactivate", data={}).status_code == 404


def test_deactivated_article_is_hidden_but_shown_with_the_filter(client: AppClient) -> None:
    create(client)
    create(client, number="3D-0002", name="Clip")
    first = articles()[0].id

    response = client.post(f"/articles/{first}/deactivate", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert [a.active for a in articles()] == [False, True]
    assert "Kabelhalter" not in client.get("/articles").text
    assert "Clip" in client.get("/articles").text
    with_inactive = client.get("/articles?inactive=1").text
    assert "Kabelhalter" in with_inactive
    assert DE["article.status.inactive"] in with_inactive
    # The detail page of a deactivated article still opens.
    assert client.get(f"/articles/{first}").status_code == 200


def test_article_can_be_reactivated(client: AppClient) -> None:
    create(client)
    article_id = articles()[0].id
    client.post(f"/articles/{article_id}/deactivate", data={})

    client.post(f"/articles/{article_id}/activate", data={})

    assert articles()[0].active is True
    assert "Kabelhalter" in client.get("/articles").text


def test_articles_cannot_be_deleted(client: AppClient) -> None:
    create(client)
    article_id = articles()[0].id

    for method in ("delete", "put"):
        assert client.request(method, f"/articles/{article_id}").status_code in (403, 405)
    assert len(articles()) == 1


# --- Test cases 6 and 7: detail page with tiles ------------------------------------------


def test_detail_shows_fields_and_five_tiles_with_count(client: AppClient) -> None:
    create(client, selling_price="12,9", weight="2", min_stock="5")
    article_id = articles()[0].id

    html = client.get(f"/articles/{article_id}").text

    assert "3D-0001" in html
    assert "Kabelhalter" in html
    assert "12,90" in html
    for section in SECTIONS:
        tile = re.search(rf'<a href="/articles/{article_id}/{section}"[^>]*>(.*?)</a>', html, re.S)
        assert tile, section
        assert DE[f"article.tile.{section}"] in tile.group(1)
        assert re.search(r">\s*0\s*<", tile.group(1)), section


def test_detail_shows_dash_for_empty_optional_fields(client: AppClient) -> None:
    create(client)

    html = client.get(f"/articles/{articles()[0].id}").text

    assert DE["article.value.none"] in html


@pytest.mark.parametrize("section", SECTIONS)
def test_every_tile_opens_a_page_for_this_article(client: AppClient, section: str) -> None:
    create(client)
    article_id = articles()[0].id

    response = client.get(f"/articles/{article_id}/{section}")

    assert response.status_code == 200
    assert DE[f"article.tile.{section}"] in response.text
    assert "Kabelhalter" in response.text
    assert DE["article.section.empty"] in response.text
    assert f'href="/articles/{article_id}"' in response.text


def test_unknown_section_gives_404(client: AppClient) -> None:
    create(client)

    assert client.get(f"/articles/{articles()[0].id}/unbekannt").status_code == 404


# --- Test case 8: login needed ------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/articles"),
        ("get", "/articles/new"),
        ("post", "/articles/new"),
        ("get", "/articles/1"),
        ("get", "/articles/1/edit"),
        ("post", "/articles/1"),
        ("post", "/articles/1/deactivate"),
        ("post", "/articles/1/activate"),
        ("get", "/articles/1/stock"),
    ],
)
def test_without_login_every_page_redirects_to_login(
    client: AppClient, method: str, path: str
) -> None:
    create(client)
    with AppClient(create_app()) as visitor:
        response = visitor.request(method, path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


# --- Test case 9: roles ---------------------------------------------------------------------


def test_employee_may_do_everything(client: AppClient, employee: AppClient) -> None:
    assert create(employee).status_code == 303
    article_id = articles()[0].id

    assert employee.get(f"/articles/{article_id}/edit").status_code == 200
    assert employee.post(f"/articles/{article_id}/deactivate", data={}).status_code in (200, 303)
    assert articles()[0].active is False


def test_reader_may_look_but_not_change(client: AppClient, reader: AppClient) -> None:
    create(client)
    article_id = articles()[0].id

    assert reader.get("/articles").status_code == 200
    detail = reader.get(f"/articles/{article_id}")
    assert detail.status_code == 200
    assert f'href="/articles/{article_id}/edit"' not in detail.text
    assert "/deactivate" not in detail.text
    assert 'href="/articles/new"' not in reader.get("/articles").text
    for section in SECTIONS:
        assert reader.get(f"/articles/{article_id}/{section}").status_code == 200

    assert reader.get("/articles/new").status_code == 403
    assert reader.get(f"/articles/{article_id}/edit").status_code == 403
    assert reader.post("/articles/new", data=article_form(number="X")).status_code == 403
    assert reader.post(f"/articles/{article_id}", data=article_form(name="Neu")).status_code == 403
    assert reader.post(f"/articles/{article_id}/deactivate", data={}).status_code == 403
    assert [(a.name, a.active) for a in articles()] == [("Kabelhalter", True)]


def test_employee_sees_the_change_buttons(client: AppClient, employee: AppClient) -> None:
    create(client)
    article_id = articles()[0].id

    html = employee.get(f"/articles/{article_id}").text

    assert f'href="/articles/{article_id}/edit"' in html
    assert f'action="/articles/{article_id}/deactivate"' in html
    assert 'href="/articles/new"' in employee.get("/articles").text


# --- Test case 10: CSRF and escaping --------------------------------------------------------


def test_forms_carry_a_form_token(client: AppClient) -> None:
    create(client)
    article_id = articles()[0].id
    for path in ("/articles/new", f"/articles/{article_id}/edit", f"/articles/{article_id}"):
        assert 'name="csrf_token"' in client.get(path).text


@pytest.mark.parametrize(
    "path", ["/articles/new", "/articles/1", "/articles/1/deactivate", "/articles/1/activate"]
)
def test_posting_without_form_token_is_refused(client: AppClient, path: str) -> None:
    create(client)
    client.csrf_token = None  # the test client adds no token any more

    response = client.post(path, data=article_form(number="3D-0009"), follow_redirects=False)

    assert response.status_code == 403
    assert [a.number for a in articles()] == ["3D-0001"]


def test_html_in_values_is_escaped(client: AppClient) -> None:
    attack = '<script>alert("x")</script>'
    create(client, number="3D-<b>1", name=attack)
    article_id = articles()[0].id

    for path in ("/articles", f"/articles/{article_id}", f"/articles/{article_id}/edit"):
        html = client.get(path).text
        assert attack not in html
        assert "&lt;script&gt;" in html
    refused = create(client, number="3D-<b>1", name=attack)
    assert attack not in refused.text


def test_templates_do_not_switch_off_escaping() -> None:
    for name in (
        "articles.html",
        "article_detail.html",
        "article_form.html",
        "article_section.html",
    ):
        source = (APP_DIR / "templates" / name).read_text(encoding="utf-8")
        assert "|safe" not in source.replace(" ", "")
        assert "autoescape" not in source


# --- Test case 11: change log ---------------------------------------------------------------


def logged() -> list[tuple[str, str]]:
    with db() as session:
        entries = session.query(ChangeLogEntry).order_by(ChangeLogEntry.id)
        return [(e.action, e.subject) for e in entries if e.action.startswith("article.")]


def test_changes_are_written_to_the_change_log(client: AppClient) -> None:
    create(client, selling_price="10")
    article_id = articles()[0].id
    client.post(
        f"/articles/{article_id}",
        data=article_form(name="Kabelhalter groß", selling_price="11"),
    )
    client.post(f"/articles/{article_id}/deactivate", data={})
    client.post(f"/articles/{article_id}/activate", data={})

    assert logged() == [
        ("article.created", "3D-0001"),
        ("article.changed", "3D-0001"),
        ("article.deactivated", "3D-0001"),
        ("article.activated", "3D-0001"),
    ]


def test_change_log_page_shows_old_and_new_values(client: AppClient) -> None:
    create(client, selling_price="10")
    client.post(
        f"/articles/{articles()[0].id}",
        data=article_form(name="Kabelhalter groß", selling_price="11,50"),
    )

    html = client.get("/settings/log").text

    assert DE["log.action.article.created"] in html
    assert DE["log.action.article.changed"] in html
    assert f"{DE['article.field.name']}: Kabelhalter → Kabelhalter groß" in html
    assert f"{DE['article.field.selling_price']}: 10 → 11,50" in html


def test_refused_or_unchanged_saves_are_not_logged(client: AppClient) -> None:
    create(client)
    article_id = articles()[0].id

    create(client)  # duplicate
    client.post(f"/articles/{article_id}", data=article_form())  # nothing changed

    assert logged() == [("article.created", "3D-0001")]


# --- Test case 12: migration ----------------------------------------------------------------


def test_article_migration_on_empty_and_filled_database(tmp_path: Path) -> None:
    database = tmp_path / "articles.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0006")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users"
            " (id, username, username_key, password_hash, role, failed_logins, created_at)"
            " VALUES (1, 'erfunden', 'erfunden', 'x', 'admin', 0, '2026-01-01 00:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT username FROM users").fetchall() == [("erfunden",)]
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone() == (0,)
        connection.execute(
            "INSERT INTO articles (number, number_key, name, active, created_at)"
            " VALUES ('3D-1', '3d-1', 'Erfunden', 1, '2026-01-01 00:00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO articles (number, number_key, name, active, created_at)"
                " VALUES ('3d-1', '3d-1', 'Doppelt', 1, '2026-01-01 00:00:00')"
            )


def test_article_migration_can_be_undone(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "articles_down.db")
    run_migrations(engine)

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0006")
    run_migrations(engine)

    with sqlite3.connect(tmp_path / "articles_down.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone() == (0,)


def test_database_itself_refuses_a_duplicate_number(client: AppClient) -> None:
    """Even if two saves pass the check in the page at the same moment."""
    create(client)
    with db() as session, pytest.raises(IntegrityError):
        session.add(
            Article(
                number="3d-0001",
                number_key="3d-0001",
                name="Doppelt",
                active=True,
                created_at=articles()[0].created_at,
            )
        )
        session.commit()


# --- Test case 13: texts from the translation files -----------------------------------------


def test_all_article_texts_exist_in_both_languages() -> None:
    german = {key for key in DE if key.startswith(("article", "articles", "log.action.article"))}
    english = {key for key in EN if key.startswith(("article", "articles", "log.action.article"))}

    assert german
    assert german == english


def test_english_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_LANGUAGE", "en")
    with signed_in_client() as client:
        create(client, selling_price="12,5")
        html = client.get(f"/articles/{articles()[0].id}").text

    assert EN["article.tile.calculations"] in html
    assert "12.50" in html
