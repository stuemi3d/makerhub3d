"""An optional image on every article and variant (issue #267). All data is invented.

The test case numbers refer to the confirmed list in the issue. The browser test (case 5)
is in tests/browser/test_article_image.py.
"""

import json
import re
import sqlite3
import struct
import zipfile
import zlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command

from app import backup, cli
from app.backup import Kind
from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.models import Article, Calculation, ChangeLogEntry
from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.test_article_purge import article_with_stock
from tests.test_articles import APP_DIR, article_form, articles, db
from tests.test_variant_bulk import form_from
from tests.test_variants import article_by_number, make_setup, variant_form

DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
STORED = re.compile(r"[0-9a-f]{32}\.(jpg|png|webp)")
TOKEN = re.compile(r'name="image_token" value="([0-9a-f]{32})"')
LIMIT = 5 * 1024 * 1024


def png(extra: int = 0) -> bytes:
    """A real 1x1 PNG; `extra` adds a harmless text chunk to make it larger."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x80\x00")
    padding = chunk(b"tEXt", b"c\x00" + b"x" * extra) if extra else b""
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + padding
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


def jpg() -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xd9"


def webp() -> bytes:
    body = b"WEBPVP8 " + struct.pack("<I", 10) + b"\x00" * 10
    return b"RIFF" + struct.pack("<I", len(body)) + body


SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"


def image_dir(data_dir: Path) -> Path:
    return data_dir / "article_images"


def stored_files(data_dir: Path) -> list[Path]:
    folder = image_dir(data_dir)
    return sorted(p for p in folder.glob("*") if p.is_file()) if folder.exists() else []


def create(client: AppClient, image: tuple[str, bytes] | None = None, **overrides: str):
    files = {"image": (image[0], image[1], "application/octet-stream")} if image else None
    return client.post(
        "/articles/new", data=article_form(**overrides), files=files, follow_redirects=False
    )


def edit(
    client: AppClient, article_id: int, image: tuple[str, bytes] | None = None, **overrides: str
):
    files = {"image": (image[0], image[1], "application/octet-stream")} if image else None
    return client.post(
        f"/articles/{article_id}",
        data=article_form(**overrides),
        files=files,
        follow_redirects=False,
    )


def image_error(html: str) -> str | None:
    match = re.search(r'<small id="image-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


def image_src(page: str) -> str | None:
    match = re.search(r'<img[^>]*class="article-image"[^>]*src="([^"]+)"', page)
    return match.group(1).replace("&amp;", "&") if match else None


def only_article() -> Article:
    [article] = articles()
    return article


# --- Test cases 1-4: upload and show ----------------------------------------------------


def test_1_png_given_when_creating_is_shown_on_the_detail_page(
    client: AppClient, data_dir: Path
) -> None:
    assert create(client, ("halter.png", png())).status_code == 303
    article = only_article()
    assert article.image_name == "halter.png"
    assert STORED.fullmatch(article.image_stored)
    [path] = stored_files(data_dir)
    assert path.name == article.image_stored
    assert path.read_bytes() == png(), "the image is stored unchanged"
    page = client.get(f"/articles/{article.id}").text
    src = image_src(page)
    assert src and src.startswith(f"/articles/{article.id}/image")
    response = client.get(src)
    assert response.status_code == 200
    assert response.content == png()


@pytest.mark.parametrize(
    ("name", "data", "media_type", "suffix"),
    [
        ("foto.jpg", jpg(), "image/jpeg", ".jpg"),
        ("foto.jpeg", jpg(), "image/jpeg", ".jpg"),
        ("foto.webp", webp(), "image/webp", ".webp"),
        ("foto.png", png(), "image/png", ".png"),
    ],
)
def test_2_jpg_png_and_webp_are_accepted(
    client: AppClient, name: str, data: bytes, media_type: str, suffix: str
) -> None:
    assert create(client, (name, data)).status_code == 303
    article = only_article()
    assert article.image_stored.endswith(suffix)
    response = client.get(f"/articles/{article.id}/image")
    assert response.headers["content-type"] == media_type


def test_3_article_without_image_has_no_image_area(client: AppClient, data_dir: Path) -> None:
    assert create(client).status_code == 303
    article = only_article()
    assert article.image_stored is None and article.image_name is None
    page = client.get(f"/articles/{article.id}").text
    assert image_src(page) is None
    assert client.get(f"/articles/{article.id}/image").status_code == 404
    assert stored_files(data_dir) == []


def test_3_form_has_an_image_field_for_files(client: AppClient) -> None:
    page = client.get("/articles/new").text
    assert re.search(r'<input type="file" name="image"[^>]*accept="[^"]*image/png', page)
    assert 'enctype="multipart/form-data"' in page
    popup = client.get("/articles/new", headers={"HX-Request": "true"}).text
    assert 'hx-encoding="multipart/form-data"' in popup
    assert DE["article.field.image"] in page


def test_4_article_list_has_no_image_column(client: AppClient) -> None:
    create(client, ("liste.png", png()))
    page = client.get("/articles").text
    assert "<img" not in page
    assert "liste.png" not in page
    assert DE["article.field.image"] not in page


# --- Test cases 6-9: replace, remove, keep ----------------------------------------------


def test_6_new_image_replaces_the_old_one_and_deletes_its_file(
    client: AppClient, data_dir: Path
) -> None:
    create(client, ("alt.png", png()))
    old = only_article()
    old_path = image_dir(data_dir) / old.image_stored
    assert edit(client, old.id, ("neu.jpg", jpg())).status_code == 303
    new = only_article()
    assert new.image_name == "neu.jpg"
    assert new.image_stored != old.image_stored
    assert not old_path.exists()
    assert [p.name for p in stored_files(data_dir)] == [new.image_stored]
    assert client.get(f"/articles/{new.id}/image").content == jpg()


def test_6_the_address_of_a_new_image_changes(client: AppClient) -> None:
    create(client, ("alt.png", png()))
    article_id = only_article().id
    before = image_src(client.get(f"/articles/{article_id}").text)
    edit(client, article_id, ("neu.png", png(10)))
    after = image_src(client.get(f"/articles/{article_id}").text)
    assert before != after, "a cached old image is not shown again"


def test_7_remove_deletes_the_image_and_its_file(client: AppClient, data_dir: Path) -> None:
    create(client, ("weg.png", png()))
    article_id = only_article().id
    form = client.get(f"/articles/{article_id}/edit").text
    assert "weg.png" in form, "the form names the current image"
    assert re.search(r'<input type="checkbox" name="image_remove"', form)
    assert edit(client, article_id, image_remove="1").status_code == 303
    article = only_article()
    assert article.image_stored is None and article.image_name is None
    assert stored_files(data_dir) == []
    assert image_src(client.get(f"/articles/{article_id}").text) is None


def test_8_saving_without_a_new_image_keeps_the_image(client: AppClient, data_dir: Path) -> None:
    create(client, ("bleibt.png", png()))
    before = only_article()
    assert edit(client, before.id, selling_price="12,50").status_code == 303
    after = only_article()
    assert after.selling_price == Decimal("12.50")
    assert (after.image_name, after.image_stored) == (before.image_name, before.image_stored)
    assert len(stored_files(data_dir)) == 1


def test_8_an_empty_file_field_keeps_the_image(client: AppClient) -> None:
    """Browsers send an empty file part when nothing was chosen."""
    create(client, ("bleibt.png", png()))
    before = only_article()
    response = client.post(
        f"/articles/{before.id}",
        data=article_form(),
        files={"image": ("", b"", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert only_article().image_stored == before.image_stored


def test_9_image_stays_chosen_when_another_field_is_wrong(
    client: AppClient, data_dir: Path
) -> None:
    create(client, number="3D-0001")
    response = create(client, ("gemerkt.png", png()), number="3D-0001")
    assert response.status_code == 422
    assert "gemerkt.png" in response.text
    assert DE["article.image.waiting"] in response.text
    token = TOKEN.search(response.text)
    assert token, "the waiting image travels in the form"
    assert len(articles()) == 1
    assert stored_files(data_dir) == [], "nothing is stored before the article is saved"

    saved = client.post(
        "/articles/new",
        data=article_form(
            number="3D-0002", image_token=token.group(1), image_token_name="gemerkt.png"
        ),
        follow_redirects=False,
    )
    assert saved.status_code == 303
    article = article_by_number("3D-0002")
    assert article.image_name == "gemerkt.png"
    assert (image_dir(data_dir) / article.image_stored).read_bytes() == png()


def test_9_an_unknown_token_is_ignored(client: AppClient, data_dir: Path) -> None:
    response = client.post(
        "/articles/new",
        data=article_form(image_token="0" * 32, image_token_name="fremd.png"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert only_article().image_stored is None
    assert stored_files(data_dir) == []


# --- Test cases 10-13: checks -----------------------------------------------------------


def test_10_image_over_5_mb_is_refused(client: AppClient, data_dir: Path) -> None:
    big = png(LIMIT)
    response = create(client, ("gross.png", big))
    assert response.status_code == 422
    assert image_error(response.text) == DE["article.error.image_too_large"]
    assert articles() == []
    assert stored_files(data_dir) == []


def test_10_image_of_exactly_5_mb_is_accepted(client: AppClient) -> None:
    data = png(LIMIT - len(png()) - 14)
    assert len(data) == LIMIT
    assert create(client, ("genau.png", data)).status_code == 303


def test_10_too_large_image_is_refused_when_editing_and_the_old_one_stays(
    client: AppClient,
) -> None:
    create(client, ("alt.png", png()))
    before = only_article()
    response = edit(client, before.id, ("gross.png", png(LIMIT)))
    assert response.status_code == 422
    assert image_error(response.text) == DE["article.error.image_too_large"]
    assert only_article().image_stored == before.image_stored


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("bild.svg", SVG),
        ("bild.png", SVG),
        ("bild.gif", GIF),
        ("bild.png", GIF),
        ("bild.png", b"nur ein Text, kein Bild"),
        ("bild.jpg", b"\x89PNG"),  # cut off: too short to be an image
    ],
)
def test_11_other_types_are_refused_by_their_content(
    client: AppClient, data_dir: Path, name: str, data: bytes
) -> None:
    response = create(client, (name, data))
    assert response.status_code == 422
    assert image_error(response.text) == DE["article.error.image_type"]
    assert articles() == []
    assert stored_files(data_dir) == []


def test_11_the_ending_does_not_matter_the_content_decides(client: AppClient) -> None:
    assert create(client, ("bild.txt", png())).status_code == 303
    assert only_article().image_stored.endswith(".png")


def test_12_empty_file_with_a_name_is_refused(client: AppClient) -> None:
    response = create(client, ("leer.png", b""))
    assert response.status_code == 422
    assert image_error(response.text) == DE["article.error.image_type"]
    assert articles() == []


def test_13_the_name_of_the_upload_is_never_a_path(client: AppClient, data_dir: Path) -> None:
    assert create(client, ("../../x.png", png())).status_code == 303
    article = only_article()
    assert STORED.fullmatch(article.image_stored)
    assert article.image_name == "x.png"
    assert [p.name for p in stored_files(data_dir)] == [article.image_stored]
    assert not (data_dir / "x.png").exists()
    assert not (data_dir.parent / "x.png").exists()


def test_13_the_name_is_shown_as_text(client: AppClient) -> None:
    create(client, ('<b>"bild".png', png()))
    article = only_article()
    page = client.get(f"/articles/{article.id}").text
    assert '<b>"bild"' not in page
    assert "&lt;b&gt;" in page


# --- Test cases 14-16: variants and templates -------------------------------------------


def test_14_single_variant_gets_replaces_and_removes_an_image(
    client: AppClient, data_dir: Path
) -> None:
    setup = make_setup(client)
    form = client.get(f"/templates/{setup.template_id}/variants/new").text
    assert re.search(r'<input type="file" name="image"', form)
    response = client.post(
        f"/templates/{setup.template_id}/variants/new",
        data=variant_form(setup),
        files={"image": ("rot.png", png(), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    variant = article_by_number("3D-0042-RT")
    assert variant.image_name == "rot.png"

    data = {**variant_form(setup)}
    assert (
        client.post(
            f"/articles/{variant.id}",
            data=data,
            files={"image": ("rot2.webp", webp(), "image/webp")},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert article_by_number("3D-0042-RT").image_name == "rot2.webp"
    assert len(stored_files(data_dir)) == 1

    data["image_remove"] = "1"
    assert client.post(f"/articles/{variant.id}", data=data).status_code in (200, 303)
    assert article_by_number("3D-0042-RT").image_stored is None
    assert stored_files(data_dir) == []


def test_14_variant_keeps_the_chosen_image_when_another_field_is_wrong(
    client: AppClient,
) -> None:
    setup = make_setup(client)
    response = client.post(
        f"/templates/{setup.template_id}/variants/new",
        data=variant_form(setup, name=""),
        files={"image": ("rot.png", png(), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "rot.png" in response.text
    assert TOKEN.search(response.text)


def test_15_templates_have_no_image_field(client: AppClient) -> None:
    setup = make_setup(client)
    assert 'name="image"' not in client.get("/templates/new").text
    assert 'name="image"' not in client.get(f"/templates/{setup.template_id}/edit").text


def test_16_all_combinations_get_no_image(client: AppClient, data_dir: Path) -> None:
    setup = make_setup(client)
    url = f"/templates/{setup.template_id}/variants/all"
    preview = client.get(url).text
    assert 'name="image"' not in preview
    response = client.post(
        url,
        data=form_from(preview),
        files={"image": ("alle.png", png(), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code in (200, 303)
    created = articles()
    assert len(created) == 4
    assert all(article.image_stored is None for article in created)
    assert stored_files(data_dir) == []


# --- Test cases 17-20: rights and delivery ----------------------------------------------


def test_17_18_reader_sees_the_image_but_cannot_change_it(
    client: AppClient, data_dir: Path
) -> None:
    create(client, ("alle.png", png()))
    article = only_article()
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        page = reader.get(f"/articles/{article.id}").text
        assert image_src(page)
        assert reader.get(f"/articles/{article.id}/image").content == png()
        assert edit(reader, article.id, ("neu.png", png(5))).status_code == 403
        assert edit(reader, article.id, image_remove="1").status_code == 403
        assert create(reader, ("neu.png", png()), number="3D-0099").status_code == 403
    assert only_article().image_stored == article.image_stored
    assert len(stored_files(data_dir)) == 1


def test_19_without_login_the_image_is_not_delivered(client: AppClient) -> None:
    create(client, ("privat.png", png()))
    article_id = only_article().id
    client.cookies.clear()
    response = client.get(f"/articles/{article_id}/image", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert png() not in response.content


def test_20_the_image_has_a_fixed_type_and_no_type_guessing(client: AppClient) -> None:
    create(client, ("bild.txt", png()))
    response = client.get(f"/articles/{only_article().id}/image")
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" not in response.headers.get("content-disposition", "")


def test_20_unknown_article_or_missing_file_gives_404(client: AppClient, data_dir: Path) -> None:
    assert client.get("/articles/999/image").status_code == 404
    create(client, ("bild.png", png()))
    for path in stored_files(data_dir):
        path.unlink()
    assert client.get(f"/articles/{only_article().id}/image").status_code == 404


# --- Test case 21: change log -----------------------------------------------------------


def image_changes() -> list[dict]:
    with db() as session:
        entries = session.query(ChangeLogEntry).order_by(ChangeLogEntry.id)
        return [
            detail
            for entry in entries
            if entry.details
            for detail in json.loads(entry.details)
            if detail["field"] == "image"
        ]


def test_21_adding_replacing_and_removing_are_in_the_change_log(client: AppClient) -> None:
    create(client, number="3D-0001")
    article_id = only_article().id
    edit(client, article_id, ("erst.png", png()))
    edit(client, article_id, ("erst.png", png(3)))  # same name, new picture
    edit(client, article_id, image_remove="1")
    assert image_changes() == [
        {"field": "image", "old": "", "new": "erst.png"},
        {"field": "image", "old": "erst.png", "new": "erst.png"},
        {"field": "image", "old": "erst.png", "new": ""},
    ]
    page = client.get("/settings/log").text
    assert f"{DE['article.field.image']}: erst.png → " in page


def test_21_image_given_when_creating_is_in_the_change_log(client: AppClient) -> None:
    create(client, ("start.png", png()))
    assert image_changes() == [{"field": "image", "old": "", "new": "start.png"}]


# --- Test cases 22-23: deleting and deactivating ----------------------------------------


def add_calculation(article_id: int) -> None:
    zero = Decimal(0)
    money = {
        name: zero
        for name in [
            "print_minutes",
            "setup_minutes",
            "rework_minutes",
            "scrap_percent",
            "margin_percent",
            "power_price",
            "machine_rate",
            "labour_rate",
            "material_per_plate",
            "power_per_plate",
            "machine_per_plate",
            "labour_cost",
            "cost_without_labour",
            "cost_with_labour",
            "suggested_price",
            "selling_price",
            "profit",
        ]
    }
    with db() as session:
        session.add(
            Calculation(
                article_id=article_id,
                created_on=date(2026, 11, 1),
                created_at=datetime(2026, 11, 1, 10, 0),
                printer_name="Drucker A",
                pieces=1,
                rounding="none",
                **money,
            )
        )
        session.commit()


@pytest.mark.parametrize("with_calculation", [False, True])
def test_22_deleting_the_article_deletes_its_image(
    client: AppClient, data_dir: Path, with_calculation: bool
) -> None:
    create(client, ("weg.png", png()))
    article_id = only_article().id
    if with_calculation:
        add_calculation(article_id)
    assert client.post(f"/articles/{article_id}/delete").status_code in (200, 204, 303)
    assert articles() == []
    assert stored_files(data_dir) == []


def test_22_purging_the_article_deletes_its_image(client: AppClient, data_dir: Path) -> None:
    article_id = article_with_stock("3D-0001")
    edit(client, article_id, ("weg.png", png()))
    assert len(stored_files(data_dir)) == 1
    response = client.post(f"/articles/{article_id}/purge", data={"number": "3D-0001"})
    assert response.status_code in (200, 204, 303)
    assert articles() == []
    assert stored_files(data_dir) == []


def test_22_a_refused_purge_keeps_the_image(client: AppClient, data_dir: Path) -> None:
    article_id = article_with_stock("3D-0001")
    edit(client, article_id, ("bleibt.png", png()))
    response = client.post(f"/articles/{article_id}/purge", data={"number": "falsch"})
    assert response.status_code == 422
    assert len(stored_files(data_dir)) == 1


def test_23_deactivating_and_activating_keep_the_image(client: AppClient, data_dir: Path) -> None:
    create(client, ("bleibt.png", png()))
    article = only_article()
    client.post(f"/articles/{article.id}/deactivate")
    client.post(f"/articles/{article.id}/activate")
    assert only_article().image_stored == article.image_stored
    assert len(stored_files(data_dir)) == 1


# --- Test case 24: backup ---------------------------------------------------------------


def test_24_the_backup_holds_the_images_and_restoring_brings_them_back(
    client: AppClient, data_dir: Path
) -> None:
    create(client, ("gesichert.png", png()))
    article = only_article()
    [path] = stored_files(data_dir)
    (image_dir(data_dir) / "pending").mkdir(exist_ok=True)
    waiting = image_dir(data_dir) / "pending" / ("a" * 32)
    waiting.write_bytes(b"waiting")
    (image_dir(data_dir) / "fremd.txt").write_text("x", encoding="utf-8")

    saved = backup.make_backup(Settings.from_env(), Kind.MANUAL, datetime(2026, 11, 24, tzinfo=UTC))
    with zipfile.ZipFile(saved) as archive:
        names = set(archive.namelist())
    assert f"article_images/{article.image_stored}" in names
    assert not any("pending" in name or "fremd" in name for name in names)

    path.unlink()
    later = image_dir(data_dir) / ("b" * 32 + ".png")
    later.write_bytes(png())
    client.close()

    assert cli.main(["restore", saved.name]) == 0

    assert path.read_bytes() == png()
    assert not later.exists(), "the images are those of the backup"
    assert waiting.exists()


def test_24_an_archive_with_a_strange_image_entry_is_refused(data_dir: Path) -> None:
    settings = Settings.from_env()
    folder = settings.backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    evil = folder / "makerhub3d-20261124-030000-manual.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.write(settings.database_path, "makerhub3d.db")
        archive.writestr("article_images/../../boese.png", b"x")
    assert cli.main(["restore", evil.name]) != 0
    assert not (data_dir.parent / "boese.png").exists()


# --- Test case 25: migration ------------------------------------------------------------


def test_25_migration_adds_empty_image_fields_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "image.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0033")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO articles (id, number, number_key, name, active, created_at, note)"
            " VALUES (1, 'A-1', 'a-1', 'Alt', 1, '2026-01-01 10:00:00', 'Notiz')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT number, note, image_name, image_stored FROM articles"
        ).fetchone()
        assert row == ("A-1", "Notiz", None, None)
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0033")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(articles)")}
        assert not {"image_name", "image_stored"} & columns
        assert connection.execute("SELECT note FROM articles").fetchone() == ("Notiz",)
    run_migrations(engine)


def test_image_texts_exist_in_german_and_english() -> None:
    for key in (
        "article.field.image",
        "article.hint.image",
        "article.image.current",
        "article.image.waiting",
        "article.image.remove",
        "article.error.image_too_large",
        "article.error.image_type",
    ):
        assert DE.get(key), key
        assert EN.get(key), key
    assert DE["article.error.image_too_large"] == "Das Bild ist zu groß (höchstens 5 MB)."
    assert DE["article.error.image_type"] == "Nur JPG, PNG oder WebP."


# --- Found in the review: the waiting image is not lost -----------------------------------


def test_9_a_refused_new_upload_keeps_the_image_that_was_waiting(client: AppClient) -> None:
    create(client, number="3D-0001")
    first = create(client, ("gemerkt.png", png()), number="3D-0001")
    token = TOKEN.search(first.text).group(1)
    second = client.post(
        "/articles/new",
        data=article_form(number="3D-0001", image_token=token, image_token_name="gemerkt.png"),
        files={"image": ("falsch.png", SVG, "image/svg+xml")},
        follow_redirects=False,
    )
    assert second.status_code == 422
    assert image_error(second.text) == DE["article.error.image_type"]
    assert TOKEN.search(second.text).group(1) == token
    assert "gemerkt.png" in second.text


def test_9_an_image_that_is_no_longer_waiting_is_not_offered_again(
    client: AppClient, data_dir: Path
) -> None:
    create(client, number="3D-0001")
    first = create(client, ("gemerkt.png", png()), number="3D-0001")
    token = TOKEN.search(first.text).group(1)
    (image_dir(data_dir) / "pending" / token).unlink()
    second = client.post(
        "/articles/new",
        data=article_form(number="3D-0001", image_token=token, image_token_name="gemerkt.png"),
        follow_redirects=False,
    )
    assert second.status_code == 422
    assert TOKEN.search(second.text) is None
    assert DE["article.image.waiting"] not in second.text
