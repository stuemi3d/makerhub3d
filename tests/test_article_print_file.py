"""An optional .gcode.3mf file on every article and variant (issue #268). All data is invented.

The test case numbers refer to the confirmed list in the issue. The browser test (case 5)
is in tests/browser/test_article_print_file.py.
"""

import json
import re
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command

from app import article_attachments, backup, calculation_files, cli
from app.backup import Kind
from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.models import ChangeLogEntry
from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.test_article_image import SVG, add_calculation, png
from tests.test_article_purge import article_with_stock
from tests.test_articles import APP_DIR, article_form, articles, db
from tests.test_slice_reader import fixture, make_zip, sliced_3mf
from tests.test_variant_bulk import form_from
from tests.test_variants import article_by_number, make_setup, variant_form

DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
NAME = "wuerfel.gcode.3mf"
STORED = re.compile(r"[0-9a-f]{32}\.gcode\.3mf")
TOKEN = re.compile(r'name="print_file_token" value="([0-9a-f]{32})"')
IMAGE_TOKEN = re.compile(r'name="image_token" value="([0-9a-f]{32})"')
SLICED = sliced_3mf()
OTHER = SLICED  # same content is fine; a new name tells them apart


def file_dir(data_dir: Path) -> Path:
    return data_dir / "article_files"


def stored_files(data_dir: Path) -> list[Path]:
    folder = file_dir(data_dir)
    return sorted(p for p in folder.glob("*") if p.is_file()) if folder.exists() else []


def files_of(**given: tuple[str, bytes]) -> dict | None:
    return {field: (n, d, "application/octet-stream") for field, (n, d) in given.items()} or None


def create(client: AppClient, print_file: tuple[str, bytes] | None = None, **overrides: str):
    files = files_of(print_file=print_file) if print_file else None
    return client.post(
        "/articles/new", data=article_form(**overrides), files=files, follow_redirects=False
    )


def edit(
    client: AppClient, article_id: int, print_file: tuple[str, bytes] | None = None, **over: str
):
    files = files_of(print_file=print_file) if print_file else None
    return client.post(
        f"/articles/{article_id}", data=article_form(**over), files=files, follow_redirects=False
    )


def file_error(html: str) -> str | None:
    match = re.search(r'<small id="print_file-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


def only_article():
    [article] = articles()
    return article


def detail(client: AppClient, article_id: int) -> str:
    return client.get(f"/articles/{article_id}").text


# --- Test cases 1-4: upload, show and download ----------------------------------------------


def test_1_2_file_given_when_creating_is_shown_and_downloaded(
    client: AppClient, data_dir: Path
) -> None:
    assert create(client, (NAME, SLICED)).status_code == 303
    article = only_article()
    assert article.print_file_name == NAME
    assert STORED.fullmatch(article.print_file_stored)
    [path] = stored_files(data_dir)
    assert path.name == article.print_file_stored
    page = detail(client, article.id)
    assert NAME in page
    assert f'href="/articles/{article.id}/file"' in page
    assert DE["article.print_file.download"] in page
    response = client.get(f"/articles/{article.id}/file")
    assert response.status_code == 200
    assert response.content == SLICED
    assert NAME in response.headers["content-disposition"]
    assert response.headers["content-disposition"].startswith("attachment")


def test_3_article_without_file_has_no_file_row(client: AppClient, data_dir: Path) -> None:
    assert create(client).status_code == 303
    article = only_article()
    assert article.print_file_stored is None and article.print_file_name is None
    page = detail(client, article.id)
    assert "/file" not in page
    assert DE["article.print_file.download"] not in page
    assert client.get(f"/articles/{article.id}/file").status_code == 404
    assert stored_files(data_dir) == []


def test_3_form_has_a_file_field(client: AppClient) -> None:
    page = client.get("/articles/new").text
    assert re.search(r'<input type="file" name="print_file"[^>]*accept="\.3mf"', page)
    assert DE["article.field.print_file"] in page


def test_4_article_list_has_no_file_column(client: AppClient) -> None:
    create(client, (NAME, SLICED))
    page = client.get("/articles").text
    assert NAME not in page
    assert DE["article.field.print_file"] not in page


# --- Test cases 6-10: replace, remove, keep, together with the image ----------------------


def test_6_new_file_replaces_the_old_one_and_deletes_it(client: AppClient, data_dir: Path) -> None:
    create(client, ("alt.gcode.3mf", SLICED))
    old = only_article()
    old_path = file_dir(data_dir) / old.print_file_stored
    assert edit(client, old.id, ("neu.gcode.3mf", OTHER)).status_code == 303
    new = only_article()
    assert new.print_file_name == "neu.gcode.3mf"
    assert new.print_file_stored != old.print_file_stored
    assert not old_path.exists()
    assert [p.name for p in stored_files(data_dir)] == [new.print_file_stored]


def test_7_remove_deletes_the_file(client: AppClient, data_dir: Path) -> None:
    create(client, (NAME, SLICED))
    article_id = only_article().id
    form = client.get(f"/articles/{article_id}/edit").text
    assert NAME in form, "the form names the current file"
    assert re.search(r'<input type="checkbox" name="print_file_remove"', form)
    assert edit(client, article_id, print_file_remove="1").status_code == 303
    article = only_article()
    assert article.print_file_stored is None and article.print_file_name is None
    assert stored_files(data_dir) == []


def test_8_saving_without_a_new_file_keeps_it(client: AppClient, data_dir: Path) -> None:
    create(client, (NAME, SLICED))
    before = only_article()
    response = client.post(
        f"/articles/{before.id}",
        data=article_form(selling_price="9,90"),
        files={"print_file": ("", b"", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert only_article().print_file_stored == before.print_file_stored
    assert len(stored_files(data_dir)) == 1


def test_9_file_and_image_stay_chosen_when_another_field_is_wrong(
    client: AppClient, data_dir: Path
) -> None:
    create(client, number="3D-0001")
    response = client.post(
        "/articles/new",
        data=article_form(number="3D-0001"),
        files=files_of(print_file=(NAME, SLICED), image=("bild.png", png())),
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert DE["article.print_file.waiting"] in response.text
    assert DE["article.image.waiting"] in response.text
    token, image_token = TOKEN.search(response.text), IMAGE_TOKEN.search(response.text)
    assert token and image_token
    assert stored_files(data_dir) == []

    saved = client.post(
        "/articles/new",
        data=article_form(
            number="3D-0002",
            print_file_token=token.group(1),
            print_file_token_name=NAME,
            image_token=image_token.group(1),
            image_token_name="bild.png",
        ),
        follow_redirects=False,
    )
    assert saved.status_code == 303
    article = article_by_number("3D-0002")
    assert (article.print_file_name, article.image_name) == (NAME, "bild.png")
    assert (file_dir(data_dir) / article.print_file_stored).read_bytes() == SLICED


def test_9_a_refused_new_file_keeps_the_waiting_one(client: AppClient) -> None:
    create(client, number="3D-0001")
    first = create(client, (NAME, SLICED), number="3D-0001")
    token = TOKEN.search(first.text).group(1)
    second = client.post(
        "/articles/new",
        data=article_form(number="3D-0001", print_file_token=token, print_file_token_name=NAME),
        files=files_of(print_file=("kaputt.gcode.3mf", b"kein zip")),
        follow_redirects=False,
    )
    assert second.status_code == 422
    assert file_error(second.text) == DE["calc.file.error.invalid"]
    assert TOKEN.search(second.text).group(1) == token


def test_10_image_and_file_are_set_replaced_and_removed_independently(
    client: AppClient, data_dir: Path
) -> None:
    response = client.post(
        "/articles/new",
        data=article_form(),
        files=files_of(print_file=(NAME, SLICED), image=("bild.png", png())),
        follow_redirects=False,
    )
    assert response.status_code == 303
    first = only_article()
    assert first.print_file_name == NAME and first.image_name == "bild.png"

    # new image, file stays
    client.post(
        f"/articles/{first.id}",
        data=article_form(),
        files=files_of(image=("neu.png", png(4))),
        follow_redirects=False,
    )
    second = only_article()
    assert second.print_file_stored == first.print_file_stored
    assert second.image_name == "neu.png"

    # remove the file, new image at the same time
    client.post(
        f"/articles/{first.id}",
        data=article_form(print_file_remove="1"),
        files=files_of(image=("drei.png", png(8))),
        follow_redirects=False,
    )
    third = only_article()
    assert third.print_file_stored is None
    assert third.image_name == "drei.png"
    assert stored_files(data_dir) == []
    assert len(list((data_dir / "article_images").glob("*.png"))) == 1


def test_10_an_error_in_the_file_keeps_the_image_waiting(client: AppClient) -> None:
    response = client.post(
        "/articles/new",
        data=article_form(),
        files=files_of(print_file=("falsch.txt", b"x"), image=("bild.png", png())),
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert IMAGE_TOKEN.search(response.text)
    assert articles() == []


# --- Test cases 11-13: checks -------------------------------------------------------------


def test_11_too_large_file_is_refused(
    client: AppClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calculation_files, "MAX_UPLOAD", 1000)
    response = create(client, (NAME, SLICED + b"0" * 2000))
    assert response.status_code == 422
    assert file_error(response.text) == DE["calc.file.error.too_large"]
    assert DE["calc.file.error.too_large"] == "Die Datei ist zu groß. Erlaubt sind höchstens 50 MB."
    assert articles() == []
    assert stored_files(data_dir) == []


def test_11_the_article_form_may_carry_a_full_file_and_a_full_image() -> None:
    limit = article_attachments.body_limit()
    assert limit >= calculation_files.MAX_UPLOAD + article_attachments.MAX_IMAGE
    assert limit <= calculation_files.MAX_UPLOAD + article_attachments.MAX_IMAGE + 2 * 1024 * 1024


@pytest.mark.parametrize(
    ("name", "data", "key"),
    [
        (
            NAME,
            make_zip({"Metadata/slice_info.config": fixture("slice_info_unsliced.config")}),
            "calc.file.error.not_sliced",
        ),
        (NAME, fixture("plate_1_excerpt.gcode"), "calc.file.error.gcode"),
        (NAME, b"PK\x03\x04 kaputt", "calc.file.error.invalid"),
        (NAME, SVG, "calc.file.error.invalid"),
        (NAME, b"", "calc.file.error.invalid"),
        ("wuerfel.3mf", SLICED, "calc.file.error.name"),
        ("wuerfel.zip", SLICED, "calc.file.error.name"),
    ],
)
def test_12_other_files_are_refused_like_in_the_calculation(
    client: AppClient, data_dir: Path, name: str, data: bytes, key: str
) -> None:
    response = create(client, (name, data))
    assert response.status_code == 422
    assert file_error(response.text) == DE[key]
    assert articles() == []
    assert stored_files(data_dir) == []


def test_12_the_name_check_ignores_case(client: AppClient) -> None:
    assert create(client, ("WUERFEL.GCODE.3MF", SLICED)).status_code == 303


def test_13_nothing_is_unpacked_and_the_name_is_never_a_path(
    client: AppClient, data_dir: Path
) -> None:
    assert create(client, ("../../x.gcode.3mf", SLICED)).status_code == 303
    article = only_article()
    assert article.print_file_name == "x.gcode.3mf"
    assert STORED.fullmatch(article.print_file_stored)
    assert [p.name for p in stored_files(data_dir)] == [article.print_file_stored]
    assert not (data_dir / "x.gcode.3mf").exists()
    assert not list(data_dir.rglob("*.gcode")), "nothing from inside the file lands on disk"
    assert not list(data_dir.rglob("Metadata"))


def test_13_the_name_is_shown_as_text(client: AppClient) -> None:
    assert create(client, ("<i>x.gcode.3mf", SLICED)).status_code == 303
    page = detail(client, only_article().id)
    assert "<i>x" not in page
    assert "&lt;i&gt;x.gcode.3mf" in page


# --- Test cases 14-16: variants and templates --------------------------------------------


def test_14_single_variant_gets_replaces_and_removes_a_file(
    client: AppClient, data_dir: Path
) -> None:
    setup = make_setup(client)
    form = client.get(f"/templates/{setup.template_id}/variants/new").text
    assert re.search(r'<input type="file" name="print_file"', form)
    response = client.post(
        f"/templates/{setup.template_id}/variants/new",
        data=variant_form(setup),
        files=files_of(print_file=(NAME, SLICED)),
        follow_redirects=False,
    )
    assert response.status_code == 303
    variant = article_by_number("3D-0042-RT")
    assert variant.print_file_name == NAME

    data = variant_form(setup)
    response = client.post(
        f"/articles/{variant.id}",
        data=data,
        files=files_of(print_file=("rot.gcode.3mf", OTHER)),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert article_by_number("3D-0042-RT").print_file_name == "rot.gcode.3mf"
    assert len(stored_files(data_dir)) == 1

    data["print_file_remove"] = "1"
    assert client.post(f"/articles/{variant.id}", data=data).status_code in (200, 303)
    assert article_by_number("3D-0042-RT").print_file_stored is None
    assert stored_files(data_dir) == []


def test_15_templates_have_no_file_field(client: AppClient) -> None:
    setup = make_setup(client)
    assert 'name="print_file"' not in client.get("/templates/new").text
    assert 'name="print_file"' not in client.get(f"/templates/{setup.template_id}/edit").text


def test_16_all_combinations_get_no_file(client: AppClient, data_dir: Path) -> None:
    setup = make_setup(client)
    url = f"/templates/{setup.template_id}/variants/all"
    preview = client.get(url).text
    assert 'name="print_file"' not in preview
    response = client.post(
        url,
        data=form_from(preview),
        files=files_of(print_file=(NAME, SLICED)),
        follow_redirects=False,
    )
    assert response.status_code in (200, 303)
    created = articles()
    assert len(created) == 4
    assert all(article.print_file_stored is None for article in created)
    assert stored_files(data_dir) == []


# --- Test cases 17-20: rights and download --------------------------------------------


def test_17_18_reader_downloads_but_cannot_change(client: AppClient, data_dir: Path) -> None:
    create(client, (NAME, SLICED))
    article = only_article()
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        assert DE["article.print_file.download"] in detail(reader, article.id)
        assert reader.get(f"/articles/{article.id}/file").content == SLICED
        assert edit(reader, article.id, ("neu.gcode.3mf", OTHER)).status_code == 403
        assert edit(reader, article.id, print_file_remove="1").status_code == 403
        assert create(reader, (NAME, SLICED), number="3D-0099").status_code == 403
    assert only_article().print_file_stored == article.print_file_stored
    assert len(stored_files(data_dir)) == 1


def test_19_without_login_the_file_is_not_delivered(client: AppClient) -> None:
    create(client, (NAME, SLICED))
    article_id = only_article().id
    client.cookies.clear()
    response = client.get(f"/articles/{article_id}/file", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_20_the_file_is_a_download_with_a_fixed_type(client: AppClient) -> None:
    create(client, (NAME, SLICED))
    response = client.get(f"/articles/{only_article().id}/file")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_20_unknown_article_or_missing_file_gives_404(client: AppClient, data_dir: Path) -> None:
    assert client.get("/articles/999/file").status_code == 404
    create(client, (NAME, SLICED))
    for path in stored_files(data_dir):
        path.unlink()
    assert client.get(f"/articles/{only_article().id}/file").status_code == 404


# --- Test case 21: change log ----------------------------------------------------------


def file_changes() -> list[dict]:
    with db() as session:
        entries = session.query(ChangeLogEntry).order_by(ChangeLogEntry.id)
        return [
            detail
            for entry in entries
            if entry.details
            for detail in json.loads(entry.details)
            if detail["field"] == "print_file"
        ]


def test_21_adding_replacing_and_removing_are_in_the_change_log(client: AppClient) -> None:
    create(client, (NAME, SLICED))
    article_id = only_article().id
    edit(client, article_id, ("zwei.gcode.3mf", OTHER))
    edit(client, article_id, print_file_remove="1")
    assert file_changes() == [
        {"field": "print_file", "old": "", "new": NAME},
        {"field": "print_file", "old": NAME, "new": "zwei.gcode.3mf"},
        {"field": "print_file", "old": "zwei.gcode.3mf", "new": ""},
    ]
    page = client.get("/settings/log").text
    assert f"{DE['article.field.print_file']}: {NAME} → zwei.gcode.3mf" in page


# --- Test cases 22-23: deleting and deactivating -----------------------------------------


@pytest.mark.parametrize("with_calculation", [False, True])
def test_22_deleting_the_article_deletes_its_file(
    client: AppClient, data_dir: Path, with_calculation: bool
) -> None:
    client.post(
        "/articles/new",
        data=article_form(),
        files=files_of(print_file=(NAME, SLICED), image=("bild.png", png())),
    )
    article_id = only_article().id
    if with_calculation:
        add_calculation(article_id)
    assert client.post(f"/articles/{article_id}/delete").status_code in (200, 204, 303)
    assert articles() == []
    assert stored_files(data_dir) == []
    assert not list((data_dir / "article_images").glob("*.png"))


def test_22_purging_the_article_deletes_its_file(client: AppClient, data_dir: Path) -> None:
    article_id = article_with_stock("3D-0001")
    edit(client, article_id, (NAME, SLICED))
    assert len(stored_files(data_dir)) == 1
    response = client.post(f"/articles/{article_id}/purge", data={"number": "3D-0001"})
    assert response.status_code in (200, 204, 303)
    assert stored_files(data_dir) == []


def test_23_deactivating_and_activating_keep_the_file(client: AppClient, data_dir: Path) -> None:
    create(client, (NAME, SLICED))
    article = only_article()
    client.post(f"/articles/{article.id}/deactivate")
    client.post(f"/articles/{article.id}/activate")
    assert only_article().print_file_stored == article.print_file_stored
    assert len(stored_files(data_dir)) == 1


# --- Test case 24: backup ------------------------------------------------------------


def test_24_the_backup_holds_the_files_and_restoring_brings_them_back(
    client: AppClient, data_dir: Path
) -> None:
    create(client, (NAME, SLICED))
    article = only_article()
    [path] = stored_files(data_dir)
    saved = backup.make_backup(Settings.from_env(), Kind.MANUAL, datetime(2026, 11, 24, tzinfo=UTC))
    with zipfile.ZipFile(saved) as archive:
        assert f"article_files/{article.print_file_stored}" in archive.namelist()
    path.unlink()
    client.close()

    assert cli.main(["restore", saved.name]) == 0

    assert path.read_bytes() == SLICED


def test_24_an_archive_with_a_strange_file_entry_is_refused(data_dir: Path) -> None:
    settings = Settings.from_env()
    folder = settings.backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    evil = folder / "makerhub3d-20261124-030000-manual.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.write(settings.database_path, "makerhub3d.db")
        archive.writestr("article_files/" + "a" * 32 + ".png", b"x")
    assert cli.main(["restore", evil.name]) != 0


# --- Test case 25: migration ---------------------------------------------------------


def test_25_migration_adds_empty_file_fields_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "file.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0034")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO articles (id, number, number_key, name, active, created_at, note,"
            " image_name) VALUES (1, 'A-1', 'a-1', 'Alt', 1, '2026-01-01 10:00:00', '', 'b.png')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT number, image_name, print_file_name, print_file_stored FROM articles"
        ).fetchone()
        assert row == ("A-1", "b.png", None, None)
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0034")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(articles)")}
        assert not {"print_file_name", "print_file_stored"} & columns
        assert connection.execute("SELECT image_name FROM articles").fetchone() == ("b.png",)
    run_migrations(engine)


def test_file_texts_exist_in_german_and_english() -> None:
    for key in (
        "article.field.print_file",
        "article.hint.print_file",
        "article.print_file.current",
        "article.print_file.waiting",
        "article.print_file.remove",
        "article.print_file.download",
    ):
        assert DE.get(key), key
        assert EN.get(key), key
