"""Calculation, part 3 (issue #182): the .gcode.3mf file is stored with the calculation.

All data is invented. The numbers of the test cases refer to the comment on issue #182.
"""

import re
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select

from app import calculation_files
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import Calculation
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_article_purge import article_with_stock
from tests.test_calculation_file import NAME, hidden, make_ready, take, upload
from tests.test_calculation_page import Ready, input_value, secret_key  # noqa: F401  (autouse)
from tests.test_calculation_store import base, stored
from tests.test_slice_reader import make_zip, sliced_3mf
from tests.test_stock import add_article, db

TOKEN = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def ready(client: AppClient) -> Ready:
    return make_ready(client)


@pytest.fixture
def folder(data_dir: Path) -> Path:
    return data_dir / "calculation_files"


def files_in(folder: Path) -> list[Path]:
    """The stored files (not the temporary ones of an upload that is not saved yet)."""
    return sorted(p for p in folder.glob("*") if p.is_file()) if folder.exists() else []


def pending_in(folder: Path) -> list[Path]:
    return sorted((folder / "pending").glob("*")) if (folder / "pending").exists() else []


def form_of(ready: Ready, article_id: int, data: bytes | None = None, **over: str) -> dict:
    """The filled form after upload and plate choice, ready to be saved."""
    page = upload(ready, data, article=str(article_id)).text
    html = take(ready, page, ["2"]).text
    form = {
        "printer": str(ready.printer),
        "article": str(article_id),
        "rows": "1",
        "print_minutes": input_value(html, "print_minutes"),
        "pieces": "2",
        "material_0": "PLA",
        "grams_0": input_value(html, "grams_0"),
        "spool_0_0": "1",
        "scrap": "5",
        "margin": "30",
        "file_name": hidden(html, "file_name"),
        "file_token": hidden(html, "file_token"),
        "action": "save",
    }
    form.update(over)
    return form


def save_file(ready: Ready, article_id: int, data: bytes | None = None, **over: str):
    form = form_of(ready, article_id, data, **over)
    return ready.client.post("/calculation/new", data=form, follow_redirects=False)


def saved_file_calculation(ready: Ready, article_id: int, data: bytes | None = None) -> int:
    response = save_file(ready, article_id, data)
    assert response.status_code == 303, response.text
    return stored()[-1].id


@pytest.fixture
def employee(ready: Ready) -> AppClient:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME, app=ready.client.app) as other:
        yield other


@pytest.fixture
def reader(ready: Ready) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME, app=ready.client.app) as other:
        yield other


# --- Cases 1-5: saving ------------------------------------------------------------------------


def test_1_saving_puts_the_file_into_the_data_folder_under_a_generated_name(
    ready: Ready, folder: Path
) -> None:
    article_id = add_article()
    saved_file_calculation(ready, article_id)
    found = files_in(folder)
    assert len(found) == 1
    assert found[0].read_bytes() == sliced_3mf()
    assert "wuerfel" not in found[0].name
    assert re.fullmatch(r"[0-9a-f]{32}\.gcode\.3mf", found[0].name)


def test_1_the_upload_page_no_longer_says_the_file_is_not_stored(ready: Ready) -> None:
    from tests.test_calculation_page import DE

    html = ready.client.get("/calculation/file").text
    assert DE["calc.file.intro"] in html
    assert "nicht gespeichert" not in DE["calc.file.intro"]


def test_2_the_calculation_keeps_the_shown_name_and_the_generated_name(
    ready: Ready, folder: Path
) -> None:
    saved_file_calculation(ready, add_article())
    item = stored()[0]
    assert item.kind == "file"
    assert item.file_name == NAME
    assert item.file_stored == files_in(folder)[0].name
    assert "/" not in item.file_stored


def test_2_a_name_with_folders_is_cleaned_and_never_a_path(ready: Ready, folder: Path) -> None:
    article_id = add_article()
    page = upload(ready, name="..\\..\\evil/../x.gcode.3mf", article=str(article_id)).text
    assert hidden(page, "file_name") == "x.gcode.3mf"
    html = take(ready, page, ["1"]).text
    form = form_of(ready, article_id, file_name=hidden(html, "file_name"))
    assert ready.client.post("/calculation/new", data=form).status_code in (200, 303)
    assert all(p.parent == folder for p in files_in(folder))


def test_3_by_hand_stores_no_file(ready: Ready, folder: Path) -> None:
    from tests.test_calculation_store import save

    assert save(ready, base()).status_code == 303
    item = stored()[0]
    assert (item.kind, item.file_name, item.file_stored) == ("hand", None, None)
    assert files_in(folder) == []


def test_3_a_file_name_without_a_token_stores_no_file(ready: Ready, folder: Path) -> None:
    article_id = add_article()
    form = form_of(ready, article_id)
    del form["file_token"]
    assert (
        ready.client.post("/calculation/new", data=form, follow_redirects=False).status_code == 303
    )
    assert stored()[0].file_stored is None
    assert files_in(folder) == []


def test_4_a_refused_save_stores_no_file_and_keeps_the_token(ready: Ready, folder: Path) -> None:
    article_id = add_article()
    form = form_of(ready, article_id, spool_0_0="")
    response = ready.client.post("/calculation/new", data=form)
    assert response.status_code == 422
    assert stored() == []
    assert files_in(folder) == []
    assert hidden(response.text, "file_token") == form["file_token"]
    # Fixed and sent again: now it is stored
    form["spool_0_0"] = "1"
    assert (
        ready.client.post("/calculation/new", data=form, follow_redirects=False).status_code == 303
    )
    assert len(files_in(folder)) == 1


def test_4_a_save_that_fails_in_the_database_leaves_no_file(
    ready: Ready, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import calc_store

    def broken(*args, **kwargs):
        raise RuntimeError("database")

    monkeypatch.setattr(calc_store, "create", broken)
    form = form_of(ready, add_article())
    with pytest.raises(RuntimeError):
        ready.client.post("/calculation/new", data=form, follow_redirects=False)
    assert files_in(folder) == []


@pytest.mark.parametrize("token", ["", "x", "../../etc/passwd", "0" * 32, "A" * 32, "0" * 31])
def test_4_an_unknown_or_forged_token_is_refused_and_touches_nothing(
    ready: Ready, folder: Path, token: str
) -> None:
    form = form_of(ready, add_article(), file_token=token)
    response = ready.client.post("/calculation/new", data=form, follow_redirects=False)
    if token == "":
        assert response.status_code == 303  # no file asked for
        return
    assert response.status_code == 422
    assert stored() == []
    assert files_in(folder) == []


def test_4_a_token_works_only_once(ready: Ready, folder: Path) -> None:
    article_id = add_article()
    form = form_of(ready, article_id)
    assert (
        ready.client.post("/calculation/new", data=form, follow_redirects=False).status_code == 303
    )
    again = ready.client.post("/calculation/new", data=form, follow_redirects=False)
    assert again.status_code == 422
    assert len(stored()) == 1
    assert len(files_in(folder)) == 1


def test_4_old_unsaved_uploads_are_cleaned_up(ready: Ready, folder: Path) -> None:
    import os
    import time

    article_id = add_article()
    upload(ready, article=str(article_id))
    take(ready, upload(ready, article=str(article_id)).text, ["1"])
    old = pending_in(folder)
    assert old
    stale = time.time() - 3 * 24 * 3600
    for path in old:
        os.utime(path, (stale, stale))
    upload(ready, article=str(article_id))
    assert not set(old) & set(pending_in(folder))


def test_5_a_copy_gets_its_own_file(ready: Ready, folder: Path) -> None:
    first = saved_file_calculation(ready, add_article())
    html = ready.client.get(f"/calculation/new?copy={first}").text
    assert TOKEN.fullmatch(hidden(html, "file_token"))
    assert hidden(html, "file_name") == NAME
    form = {
        "printer": str(ready.printer),
        "article": str(stored()[0].article_id),
        "rows": "1",
        "print_minutes": "60",
        "pieces": "2",
        "material_0": "PLA",
        "grams_0": "100",
        "spool_0_0": "1",
        "scrap": "5",
        "margin": "30",
        "file_name": hidden(html, "file_name"),
        "file_token": hidden(html, "file_token"),
        "action": "save",
    }
    assert (
        ready.client.post("/calculation/new", data=form, follow_redirects=False).status_code == 303
    )
    one, two = stored()
    assert one.file_stored != two.file_stored
    assert len(files_in(folder)) == 2
    delete = ready.client.post(f"/calculation/{one.id}/delete", follow_redirects=False)
    assert delete.status_code == 303
    assert [p.name for p in files_in(folder)] == [two.file_stored]


def test_5_a_copy_of_a_calculation_without_file_has_no_token(ready: Ready) -> None:
    from tests.test_calculation_store import saved_one

    first = saved_one(ready, base())
    html = ready.client.get(f"/calculation/new?copy={first}").text
    assert 'name="file_token"' not in html


# --- Cases 6-8: checking the file --------------------------------------------------------------


def test_6_a_file_over_the_limit_is_refused_and_nothing_is_kept(
    ready: Ready, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calculation_files, "MAX_UPLOAD", 1000)
    response = upload(ready, sliced_3mf() + b"0" * 2000)
    assert response.status_code in (413, 422)
    assert pending_in(folder) == [] and files_in(folder) == []


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"not a zip", NAME),
        (sliced_3mf(), "wuerfel.zip"),
        (b"", NAME),
        (sliced_3mf(with_gcode=False)[:20], NAME),
        (make_zip({"readme.txt": b"hello"}), NAME),
    ],
)
def test_7_a_file_that_is_not_a_valid_gcode_3mf_is_refused_and_nothing_is_kept(
    ready: Ready, folder: Path, data: bytes, name: str
) -> None:
    response = upload(ready, data, name=name)
    assert response.status_code == 422
    assert pending_in(folder) == [] and files_in(folder) == []


def test_8_the_stored_file_is_never_unpacked(ready: Ready, folder: Path, data_dir: Path) -> None:
    saved_file_calculation(ready, add_article())
    everything = [p for p in data_dir.rglob("*") if p.is_file()]
    names = {p.name for p in everything}
    assert not any(n.endswith((".gcode", ".config", ".xml")) for n in names)
    assert files_in(folder)[0].read_bytes() == sliced_3mf()


# --- Cases 9-11: view and download -------------------------------------------------------------


def test_9_the_download_is_the_uploaded_file_with_a_good_name(ready: Ready) -> None:
    article_id = add_article("3D-0042")
    calculation_id = saved_file_calculation(ready, article_id)
    response = ready.client.get(f"/calculation/{calculation_id}/file")
    assert response.status_code == 200
    assert response.content == sliced_3mf()
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "3D-0042-" in disposition and disposition.rstrip('"').endswith(".gcode.3mf")
    assert stored()[0].file_stored not in disposition


def test_9_the_view_shows_the_file_name_and_a_download_link(ready: Ready) -> None:
    calculation_id = saved_file_calculation(ready, add_article())
    html = ready.client.get(f"/calculation/{calculation_id}").text
    assert NAME in html
    assert f'href="/calculation/{calculation_id}/file"' in html


def test_10_without_a_file_there_is_no_download(ready: Ready) -> None:
    from tests.test_calculation_store import saved_one

    first = saved_one(ready, base())
    html = ready.client.get(f"/calculation/{first}").text
    assert f"/calculation/{first}/file" not in html
    assert ready.client.get(f"/calculation/{first}/file").status_code == 404


def test_10_an_unknown_calculation_or_a_missing_file_on_disk_gives_404(
    ready: Ready, folder: Path
) -> None:
    assert ready.client.get("/calculation/999/file").status_code == 404
    assert ready.client.get("/calculation/abc/file").status_code in (404, 422)
    calculation_id = saved_file_calculation(ready, add_article())
    for path in files_in(folder):
        path.unlink()
    assert ready.client.get(f"/calculation/{calculation_id}/file").status_code == 404


def test_11_everybody_who_may_view_may_download_but_nobody_without_login(
    ready: Ready, reader: AppClient
) -> None:
    calculation_id = saved_file_calculation(ready, add_article())
    assert reader.get(f"/calculation/{calculation_id}/file").status_code == 200
    with AppClient(create_app()) as anonymous:
        response = anonymous.get(f"/calculation/{calculation_id}/file", follow_redirects=False)
        assert response.status_code in (302, 303, 401, 403)
        assert response.content != sliced_3mf()


def test_11_a_stored_name_in_the_database_cannot_reach_other_files(
    ready: Ready, data_dir: Path
) -> None:
    calculation_id = saved_file_calculation(ready, add_article())
    secret = data_dir / "secret.txt"
    secret.write_text("secret")
    with db() as session, session.begin():
        session.get(Calculation, calculation_id).file_stored = "../secret.txt"
    assert ready.client.get(f"/calculation/{calculation_id}/file").status_code == 404


def test_11_saving_with_a_file_needs_the_right_to_edit(
    ready: Ready, reader: AppClient, folder: Path
) -> None:
    form = form_of(ready, add_article())
    assert reader.post("/calculation/new", data=form, follow_redirects=False).status_code == 403
    assert files_in(folder) == []


# --- Cases 12-15: delete and edit --------------------------------------------------------------


def test_12_deleting_the_calculation_deletes_its_file(ready: Ready, folder: Path) -> None:
    calculation_id = saved_file_calculation(ready, add_article())
    response = ready.client.post(f"/calculation/{calculation_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert stored() == []
    assert files_in(folder) == []


def test_12_a_refused_delete_keeps_the_file(
    ready: Ready, folder: Path, employee: AppClient, reader: AppClient
) -> None:
    calculation_id = saved_file_calculation(ready, add_article())
    assert reader.post(f"/calculation/{calculation_id}/delete").status_code == 403
    assert len(files_in(folder)) == 1


def test_13_purging_an_article_deletes_the_files_of_all_its_calculations(
    ready: Ready, folder: Path
) -> None:
    article_id = article_with_stock("3D-0001")
    saved_file_calculation(ready, article_id)
    saved_file_calculation(ready, article_id)
    other = add_article("3D-0002")
    saved_file_calculation(ready, other)
    assert len(files_in(folder)) == 3
    response = ready.client.post(
        f"/articles/{article_id}/purge", data={"number": "3D-0001"}, follow_redirects=False
    )
    assert response.status_code in (302, 303)
    assert len(files_in(folder)) == 1
    assert [c.article_id for c in stored()] == [other]


def test_13_deleting_an_article_without_stock_deletes_the_files_too(
    ready: Ready, folder: Path
) -> None:
    article_id = add_article("3D-0003")
    saved_file_calculation(ready, article_id)
    response = ready.client.post(f"/articles/{article_id}/delete", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert files_in(folder) == []


def test_14_a_file_that_is_already_gone_does_not_break_deleting(ready: Ready, folder: Path) -> None:
    calculation_id = saved_file_calculation(ready, add_article())
    for path in files_in(folder):
        path.unlink()
    response = ready.client.post(f"/calculation/{calculation_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert stored() == []


def test_15_editing_keeps_the_file(ready: Ready, folder: Path) -> None:
    calculation_id = saved_file_calculation(ready, add_article())
    before = stored()[0]
    form = {
        "printer": str(ready.printer),
        "rows": "1",
        "print_minutes": "60",
        "pieces": "3",
        "material_0": "PLA",
        "grams_0": "80",
        "spool_0_0": "1",
        "scrap": "5",
        "margin": "30",
        "action": "save",
    }
    response = ready.client.post(
        f"/calculation/{calculation_id}/edit", data=form, follow_redirects=False
    )
    assert response.status_code == 303
    after = stored()[0]
    assert (after.pieces, after.file_stored, after.file_name) == (
        3,
        before.file_stored,
        before.file_name,
    )
    assert len(files_in(folder)) == 1
    # A token sent to the edit page replaces nothing
    form["file_token"] = "0" * 32
    ready.client.post(f"/calculation/{calculation_id}/edit", data=form, follow_redirects=False)
    assert stored()[0].file_stored == before.file_stored


# --- Case 16: migration ------------------------------------------------------------------------


def test_16_migration_adds_the_columns_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "files.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0026")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO articles (number, number_key, name, active, created_at)"
            " VALUES ('A-1', 'a-1', 'Alt', 1, '2026-01-01 10:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(calculations)")}
        assert {"file_name", "file_stored"} <= columns
        assert connection.execute("SELECT name FROM articles").fetchall() == [("Alt",)]
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0026")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(calculations)")}
        assert not columns & {"file_name", "file_stored"}
        assert connection.execute("SELECT name FROM articles").fetchall() == [("Alt",)]
    run_migrations(engine)


def test_16_existing_calculations_stay_without_a_file(ready: Ready) -> None:
    from tests.test_calculation_store import saved_one

    saved_one(ready, base())
    item = stored()[0]
    assert (item.file_name, item.file_stored) == (None, None)
    with db() as session:
        assert session.scalars(select(Calculation.id)).all()
