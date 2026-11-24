"""A free note on every article and variant (issue #266). All data is invented.

The test case numbers refer to the confirmed list in the issue. The browser test (case 15)
is in tests/browser/test_article_note.py.
"""

import html
import json
import re
import sqlite3
from pathlib import Path

from alembic import command

from app.db import alembic_config, make_engine, run_migrations
from app.models import ChangeLogEntry
from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.test_articles import APP_DIR, article_form, articles, create, db, field_error
from tests.test_variant_bulk import form_from
from tests.test_variants import article_by_number, make_setup, make_variant

DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))


def detail(client: AppClient, article_id: int) -> str:
    return client.get(f"/articles/{article_id}").text


def note_block(page: str) -> str | None:
    """The text of the note on the detail page, or None when there is no note area."""
    match = re.search(r'<dd class="article-note">(.*?)</dd>', page, re.DOTALL)
    return html.unescape(match.group(1)) if match else None


def edit(client: AppClient, article_id: int, **overrides: str):
    return client.post(
        f"/articles/{article_id}", data=article_form(**overrides), follow_redirects=False
    )


# --- Test cases 1-3: create with and without a note --------------------------------------


def test_note_given_when_creating_is_shown_on_the_detail_page(client: AppClient) -> None:
    assert create(client, note="Mit 0,2er Düse drucken").status_code == 303
    article = articles()[0]
    assert article.note == "Mit 0,2er Düse drucken"
    assert note_block(detail(client, article.id)) == "Mit 0,2er Düse drucken"


def test_line_breaks_of_the_note_are_kept(client: AppClient) -> None:
    create(client, note="Erste Zeile\r\nZweite Zeile\nDritte Zeile")
    article = articles()[0]
    assert article.note == "Erste Zeile\nZweite Zeile\nDritte Zeile"
    assert note_block(detail(client, article.id)) == "Erste Zeile\nZweite Zeile\nDritte Zeile"


def test_article_without_note_has_no_empty_note_area(client: AppClient) -> None:
    assert create(client).status_code == 303
    article = articles()[0]
    assert article.note == ""
    page = detail(client, article.id)
    assert note_block(page) is None
    assert DE["article.field.note"] not in page


def test_form_has_a_note_field(client: AppClient) -> None:
    page = client.get("/articles/new").text
    assert re.search(r'<textarea[^>]*name="note"', page)
    assert DE["article.field.note"] in page


# --- Test cases 4-5, 8: variants --------------------------------------------------------


def test_single_variant_may_get_a_note_when_created(client: AppClient) -> None:
    setup = make_setup(client)
    assert "note" in client.get(f"/templates/{setup.template_id}/variants/new").text, (
        "the variant form has the note field"
    )
    assert make_variant(client, setup, note="Nur in Rot").status_code == 303
    assert article_by_number("3D-0042-RT").note == "Nur in Rot"


def test_all_combinations_get_no_note(client: AppClient) -> None:
    setup = make_setup(client)
    url = f"/templates/{setup.template_id}/variants/all"
    preview = client.get(url).text
    response = client.post(
        url, data=form_from(preview, note="darf nicht ankommen"), follow_redirects=False
    )
    assert response.status_code in (200, 303)
    created = articles()
    assert len(created) == 4
    assert all(article.note == "" for article in created)


def test_note_of_a_variant_can_be_edited(client: AppClient) -> None:
    setup = make_setup(client)
    make_variant(client, setup)
    variant = article_by_number("3D-0042-RT")
    form = client.get(f"/articles/{variant.id}/edit").text
    assert re.search(r'<textarea[^>]*name="note"', form)
    data = {
        "number": "3D-0042-RT",
        "name": "Halter A rot L",
        "selling_price": "",
        "min_stock": "",
        f"attr_{setup.colour_id}": str(setup.values["rot"]),
        f"attr_{setup.size_id}": str(setup.values["L"]),
        "note": "Variante mit Notiz",
    }
    assert client.post(f"/articles/{variant.id}", data=data).status_code in (200, 303)
    assert article_by_number("3D-0042-RT").note == "Variante mit Notiz"


# --- Test cases 6-7: edit and clear -----------------------------------------------------


def test_changed_note_is_shown_and_logged_with_old_and_new_text(client: AppClient) -> None:
    create(client, note="Alte Notiz")
    article_id = articles()[0].id
    form = client.get(f"/articles/{article_id}/edit").text
    assert "Alte Notiz" in form, "the form shows the stored note"
    assert edit(client, article_id, note="Neue Notiz").status_code == 303
    assert note_block(detail(client, article_id)) == "Neue Notiz"
    with db() as session:
        entry = (
            session.query(ChangeLogEntry)
            .filter_by(action="article.changed")
            .order_by(ChangeLogEntry.id.desc())
            .first()
        )
        assert {"field": "note", "old": "Alte Notiz", "new": "Neue Notiz"} in json.loads(
            entry.details
        )


def test_change_log_page_names_the_note(client: AppClient) -> None:
    create(client, note="Alte Notiz")
    edit(client, articles()[0].id, note="Neue Notiz")
    page = client.get("/settings/log").text
    assert f"{DE['article.field.note']}: Alte Notiz → Neue Notiz" in page


def test_cleared_note_removes_it(client: AppClient) -> None:
    create(client, note="Weg damit")
    article_id = articles()[0].id
    assert edit(client, article_id, note="").status_code == 303
    assert articles()[0].note == ""
    assert note_block(detail(client, article_id)) is None


# --- Test cases 9-11: checks ------------------------------------------------------------


def test_too_long_note_is_refused_and_kept_in_the_form(client: AppClient) -> None:
    text = "x" * 2001
    response = create(client, note=text)
    assert response.status_code == 422
    assert field_error(response.text, "note") == DE["article.error.note_too_long"]
    assert text in response.text, "the entered note stays in the form"
    assert articles() == []


def test_note_of_exactly_2000_characters_is_accepted(client: AppClient) -> None:
    assert create(client, note="x" * 2000).status_code == 303
    assert len(articles()[0].note) == 2000


def test_too_long_note_is_refused_when_editing(client: AppClient) -> None:
    create(client, note="Bleibt")
    article_id = articles()[0].id
    response = edit(client, article_id, note="y" * 2001)
    assert response.status_code == 422
    assert field_error(response.text, "note") == DE["article.error.note_too_long"]
    assert articles()[0].note == "Bleibt"


def test_spaces_around_the_note_are_removed(client: AppClient) -> None:
    create(client, note="   Mit Rand   \n")
    assert articles()[0].note == "Mit Rand"


def test_note_of_only_spaces_counts_as_empty(client: AppClient) -> None:
    create(client, note="   \n  \t ")
    article = articles()[0]
    assert article.note == ""
    assert note_block(detail(client, article.id)) is None


def test_control_characters_in_the_note_are_refused(client: AppClient) -> None:
    response = create(client, note="Text\x00mit Steuerzeichen")
    assert response.status_code == 422
    assert field_error(response.text, "note") == DE["article.error.note_chars"]


def test_html_in_the_note_is_shown_as_text(client: AppClient) -> None:
    create(client, note="<script>alert(1)</script>")
    page = detail(client, articles()[0].id)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


# --- Test case 12: read-only role -------------------------------------------------------


def test_reader_sees_the_note_but_cannot_change_it(client: AppClient) -> None:
    create(client, note="Für alle sichtbar")
    article_id = articles()[0].id
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        assert note_block(detail(reader, article_id)) == "Für alle sichtbar"
        assert reader.get(f"/articles/{article_id}/edit").status_code == 403
        response = edit(reader, article_id, note="Geändert")
        assert response.status_code == 403
        assert reader.post("/articles/new", data=article_form(note="x")).status_code == 403
    assert articles()[0].note == "Für alle sichtbar"


# --- Test cases 13-14: migration and list -----------------------------------------------


def test_migration_gives_existing_articles_an_empty_note_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "note.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0032")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO articles (id, number, number_key, name, selling_price, active, created_at)"
            " VALUES (1, 'A-1', 'a-1', 'Alt', '9.90', 1, '2026-01-01 10:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT number, name, selling_price, note FROM articles"
        ).fetchone()
        assert row == ("A-1", "Alt", "9.90", "")
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0032")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(articles)")}
        assert "note" not in columns
        assert connection.execute("SELECT number FROM articles").fetchone() == ("A-1",)
    run_migrations(engine)


def test_article_list_has_no_note_column(client: AppClient) -> None:
    create(client, note="Nicht in der Liste")
    page = client.get("/articles").text
    assert "Nicht in der Liste" not in page
    assert DE["article.field.note"] not in page


def test_note_texts_exist_in_german_and_english() -> None:
    for key in (
        "article.field.note",
        "article.error.note_too_long",
        "article.error.note_chars",
        "article.hint.note",
    ):
        assert DE.get(key), key
        assert EN.get(key), key
