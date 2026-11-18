"""Database backup (issue #9, test cases 1-17)."""

import json
import os
import shutil
import sqlite3
import stat
import threading
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app import backup, cli
from app.backup import BackupError, Kind
from app.config import Settings
from app.db import make_engine, run_migrations
from app.main import create_app
from app.models import ChangeLogEntry, User
from tests.auth import (
    EMPLOYEE_NAME,
    OTHER_PASSWORD,
    READER_NAME,
    AppClient,
    add_user,
    client_as,
    log_in,
)

BERLIN = ZoneInfo("Europe/Berlin")
ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))


def settings() -> Settings:
    return Settings.from_env()


def make_database() -> Path:
    """A migrated database with the data directory of the current test."""
    current = settings()
    current.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(current.database_path)
    run_migrations(engine)
    engine.dispose()
    return current.database_path


def database_of(path: Path) -> Path:
    """The database inside a backup: a zip archive is unpacked next to it, an old .db is as is."""
    if path.suffix != ".zip":
        return path
    target = path.with_suffix(".extracted.db")
    with zipfile.ZipFile(path) as archive, archive.open("makerhub3d.db") as source:
        target.write_bytes(source.read())
    return target


def usernames(database: Path) -> set[str]:
    database = database_of(database)
    with Session(make_engine(database)) as session:
        return {user.username for user in session.query(User)}


def put_backup(kind: Kind, moment: datetime) -> Path:
    return backup.create_backup(make_database(), settings().backup_dir, kind, now=moment)


def at(year: int, month: int, day: int, hour: int = 3, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def names(kind: Kind | None = None) -> list[str]:
    return sorted(
        item.name
        for item in backup.list_backups(settings().backup_dir)
        if kind is None or item.kind == kind
    )


# --- Test cases 1-3: create a backup ----------------------------------------------------


def test_backup_is_a_file_with_the_date_in_its_name_and_the_same_data() -> None:
    database = make_database()
    add_user("anna", "admin")

    path = backup.create_backup(
        database, settings().backup_dir, Kind.MANUAL, now=at(2026, 11, 13, 2, 15)
    )

    assert path.parent == settings().backup_dir
    assert "20261113" in path.name
    assert usernames(path) == usernames(database) == {"anna"}


def test_backup_while_the_app_writes_is_complete_and_intact() -> None:
    database = make_database()
    stop = threading.Event()
    written = []

    def writer() -> None:
        connection = sqlite3.connect(database, timeout=30)
        number = 0
        while not stop.is_set():
            connection.execute(
                "INSERT INTO login_log (created_at, event) VALUES ('2026-01-01 00:00:00', 'failed')"
            )
            connection.commit()
            number += 1
        written.append(number)
        connection.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        paths = [
            backup.create_backup(database, settings().backup_dir, Kind.MANUAL, now=at(2026, 1, n))
            for n in range(1, 4)
        ]
    finally:
        stop.set()
        thread.join()

    assert written[0] > 0
    for path in paths:
        copy = sqlite3.connect(database_of(path))
        assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        copy.close()


def test_backup_file_and_folder_are_private() -> None:
    database = make_database()

    path = backup.create_backup(database, settings().backup_dir, Kind.MANUAL, now=at(2026, 11, 13))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_no_half_written_file_is_left_behind() -> None:
    database = make_database()

    backup.create_backup(database, settings().backup_dir, Kind.MANUAL, now=at(2026, 11, 13))

    assert [p.name for p in settings().backup_dir.iterdir()] == names()


def test_unwritable_folder_raises_a_clear_error(tmp_path: Path) -> None:
    database = make_database()
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x", encoding="utf-8")

    with pytest.raises(BackupError):
        backup.create_backup(database, blocker / "backups", Kind.MANUAL, now=at(2026, 11, 13))


def test_nightly_failure_is_logged_without_paths_and_does_not_crash(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_database()
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x", encoding="utf-8")
    broken = Settings(data_dir=settings().data_dir)
    monkeypatch.setattr(Settings, "backup_dir", property(lambda self: blocker / "backups"))

    assert backup.run_nightly(broken, now=at(2026, 11, 13)) is None

    err = capsys.readouterr().err
    assert err.strip()
    assert str(tmp_path) not in err
    assert str(settings().data_dir) not in err


# --- Test cases 4-7: nightly run and clean-up ------------------------------------------


def test_nightly_run_creates_one_daily_backup_per_day() -> None:
    make_database()

    first = backup.run_nightly(settings(), now=at(2026, 11, 13, 2, 0))
    second = backup.run_nightly(settings(), now=at(2026, 11, 13, 2, 30))

    assert first is not None
    assert second is None
    assert len(names(Kind.DAILY)) == 1


def test_next_day_gets_its_own_backup() -> None:
    make_database()

    backup.run_nightly(settings(), now=at(2026, 11, 13, 2, 0))
    assert backup.run_nightly(settings(), now=at(2026, 11, 14, 2, 0)) is not None

    assert len(names(Kind.DAILY)) == 2


def test_a_manual_or_pre_migration_backup_does_not_replace_the_nightly_one() -> None:
    make_database()
    put_backup(Kind.MANUAL, at(2026, 11, 13, 1, 0))
    put_backup(Kind.PREMIGRATION, at(2026, 11, 13, 1, 5))

    assert backup.run_nightly(settings(), now=at(2026, 11, 13, 2, 0)) is not None


def test_nightly_run_is_at_three_in_the_morning_local_time() -> None:
    # 2026-11-13 01:00 in Berlin (winter, UTC+1) -> two hours until 03:00.
    now = datetime(2026, 11, 13, 1, 0, tzinfo=BERLIN)
    assert backup.seconds_until_next_run(now, BERLIN) == 2 * 3600
    # Just after 03:00 -> the next day.
    now = datetime(2026, 11, 13, 3, 0, 1, tzinfo=BERLIN)
    assert backup.seconds_until_next_run(now, BERLIN) == pytest.approx(24 * 3600 - 1)


def test_the_newest_14_daily_backups_are_kept() -> None:
    day = at(2026, 11, 13)
    for back in range(20):
        put_backup(Kind.DAILY, day - timedelta(days=back))

    backup.prune(settings().backup_dir, BERLIN)

    kept = backup.list_backups(settings().backup_dir)
    newest_14 = {(day - timedelta(days=back)).strftime("%Y%m%d") for back in range(14)}
    dates = {item.created_at.strftime("%Y%m%d") for item in kept}
    assert dates >= newest_14
    assert "20261030" not in dates  # day 15 and not the last of its month
    assert "20261025" not in dates


def test_the_last_backup_of_each_month_is_kept_up_to_12_months() -> None:
    # One backup on the 5th and one on the 20th of the last 15 months.
    for month_back in range(15):
        year, month = divmod(2026 * 12 + 10 - month_back, 12)
        put_backup(Kind.DAILY, at(year, month + 1, 5))
        put_backup(Kind.DAILY, at(year, month + 1, 20))

    backup.prune(settings().backup_dir, BERLIN)

    kept = {
        item.created_at.strftime("%Y%m%d") for item in backup.list_backups(settings().backup_dir)
    }
    assert "20261120" in kept
    # The newest 14 days with a backup reach back to May; older ones only as monthly backups.
    assert "20260420" in kept  # the last one of April
    assert "20260405" not in kept  # not the last one of its month, and not recent
    assert "20251220" in kept  # 11 months back: the 12th month
    assert "20251120" not in kept  # 12 months back: too old
    assert "20241120" not in kept


def test_pruning_never_touches_foreign_files() -> None:
    make_database()
    for back in range(20):
        put_backup(Kind.DAILY, at(2026, 11, 13) - timedelta(days=back))
    folder = settings().backup_dir
    foreign = [folder / "notes.txt", folder / "makerhub3d-wrong.db", folder / "other.db"]
    for path in foreign:
        path.write_text("keep", encoding="utf-8")

    backup.prune(folder, BERLIN)

    assert all(path.exists() for path in foreign)


def test_pruning_removes_old_leftovers_of_interrupted_backups_only() -> None:
    make_database()
    folder = settings().backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    old, fresh = folder / ".tmp-old.part", folder / ".tmp-fresh.part"
    old.write_text("x", encoding="utf-8")
    fresh.write_text("x", encoding="utf-8")
    two_hours_ago = old.stat().st_mtime - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))

    backup.prune(folder, BERLIN)

    assert not old.exists()
    assert fresh.exists()


# --- Test cases 8-10: backup before a migration -----------------------------------------


def test_pending_migration_makes_a_backup_first(monkeypatch: pytest.MonkeyPatch) -> None:
    database = make_database()
    monkeypatch.setattr("app.db.head_revision", lambda: "9999")
    calls: list[str] = []

    engine = make_engine(database)
    run_migrations(engine, before_migration=lambda: calls.append("backup"))
    engine.dispose()

    assert calls == ["backup"]


def test_the_start_of_the_app_makes_the_pre_migration_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_database()
    add_user("anna", "admin")
    monkeypatch.setattr("app.db.head_revision", lambda: "9999")

    with AppClient(create_app()):
        pass

    [item] = backup.list_backups(settings().backup_dir)
    assert item.kind == Kind.PREMIGRATION
    assert usernames(settings().backup_dir / item.name) == {"anna"}


def test_no_pre_migration_backup_when_up_to_date_or_new(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    engine = make_engine(settings().data_dir / "fresh.db")
    settings().data_dir.mkdir(parents=True, exist_ok=True)

    run_migrations(engine, before_migration=lambda: calls.append("new"))
    run_migrations(engine, before_migration=lambda: calls.append("current"))
    engine.dispose()

    assert calls == []


def test_the_newest_5_pre_migration_backups_are_kept() -> None:
    for day in range(1, 9):
        put_backup(Kind.PREMIGRATION, at(2026, 11, day, 12))

    backup.prune(settings().backup_dir, BERLIN)

    assert names(Kind.PREMIGRATION) == [
        f"makerhub3d-202611{day:02d}-120000-premigration.zip" for day in range(4, 9)
    ]


def test_failed_backup_before_migration_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    make_database()
    monkeypatch.setattr("app.db.head_revision", lambda: "9999")

    def fail(*args: object, **kwargs: object) -> None:
        raise BackupError("no space")

    monkeypatch.setattr("app.backup.create_backup", fail)

    with pytest.raises(BackupError), AppClient(create_app()):
        pass

    assert capsys.readouterr().err.strip()


# --- Test cases 11-15: page and download for admins -------------------------------------


def test_admin_sees_the_list_and_downloads_a_valid_database(client: AppClient) -> None:
    put_backup(Kind.DAILY, at(2026, 11, 12))
    put_backup(Kind.MANUAL, at(2026, 11, 13))

    page = client.get("/settings/backups")
    assert page.status_code == 200
    for item in backup.list_backups(settings().backup_dir):
        assert item.name in page.text
        assert f"/settings/backups/{item.name}/download" in page.text

    name = "makerhub3d-20261112-030000-daily.zip"
    download = client.get(f"/settings/backups/{name}/download")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    copy = settings().data_dir / "downloaded.zip"
    copy.write_bytes(download.content)
    assert usernames(copy)


@pytest.mark.parametrize("username", [EMPLOYEE_NAME, READER_NAME])
def test_other_roles_have_no_access(username: str) -> None:
    role = "employee" if username == EMPLOYEE_NAME else "read_only"
    path = put_backup(Kind.DAILY, at(2026, 11, 12))
    add_user(username, role)

    with client_as(username) as other:
        assert other.get("/settings/backups").status_code == 403
        assert other.get(f"/settings/backups/{path.name}/download").status_code == 403
        assert other.post("/settings/backups", data={}).status_code == 403


def test_visitors_who_are_not_logged_in_are_sent_to_the_login(anon_client: AppClient) -> None:
    add_user("anna", "admin")
    assert anon_client.get("/settings/backups", follow_redirects=False).status_code == 303
    response = anon_client.get(
        "/settings/backups/makerhub3d-20261112-030000-daily.zip/download", follow_redirects=False
    )
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


@pytest.mark.parametrize(
    "name",
    [
        "..%2Fmakerhub3d.db",
        "..%2F..%2Fetc%2Fpasswd",
        "makerhub3d-20990101-000000-daily.zip",  # fits the pattern but does not exist
        "notes.txt",
    ],
)
def test_unknown_or_dangerous_names_give_not_found(client: AppClient, name: str) -> None:
    put_backup(Kind.DAILY, at(2026, 11, 12))
    (settings().backup_dir / "notes.txt").write_text("secret", encoding="utf-8")

    assert client.get(f"/settings/backups/{name}/download").status_code == 404


def test_download_and_manual_backup_are_in_the_change_log(client: AppClient) -> None:
    client.post("/settings/backups", data={})
    [item] = backup.list_backups(settings().backup_dir)
    client.get(f"/settings/backups/{item.name}/download")

    with Session(make_engine(settings().database_path)) as session:
        actions = [entry.action for entry in session.query(ChangeLogEntry).order_by("id")]
        downloaded = session.query(ChangeLogEntry).filter_by(action="backup.downloaded").one()
    assert "backup.created" in actions
    assert downloaded.subject == item.name


def test_manual_backup_button_creates_a_backup_and_needs_the_form_token(
    client: AppClient,
) -> None:
    response = client.post("/settings/backups", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert len(names(Kind.MANUAL)) == 1

    client.csrf_token = None
    assert client.post("/settings/backups", data={}, follow_redirects=False).status_code == 403
    assert len(names(Kind.MANUAL)) == 1


def test_page_warns_that_backups_are_confidential(client: AppClient) -> None:
    assert DE["backup.hint.confidential"] in client.get("/settings/backups").text


def test_settings_tabs_link_to_the_backup_page(client: AppClient) -> None:
    assert 'href="/settings/backups"' in client.get("/settings").text


# --- Test cases 16-17: restore and guide ------------------------------------------------


def test_restoring_a_backup_brings_the_old_data_back_and_the_app_starts() -> None:
    database = make_database()
    add_user("vorher", "admin")
    saved = backup.create_backup(database, settings().backup_dir, Kind.MANUAL, now=at(2026, 11, 13))
    add_user("nachher", "admin")
    assert usernames(database) == {"vorher", "nachher"}

    # The steps of the guide: app stopped (no engine open here), restore with the command.
    assert cli.main(["restore", saved.name]) == 0

    with AppClient(create_app()) as restarted:
        assert log_in(restarted, "vorher", OTHER_PASSWORD).status_code == 200
        assert restarted.get("/").status_code == 200
    assert usernames(database) == {"vorher"}


def test_the_guide_names_the_steps() -> None:
    guide = (ROOT / "docs" / "wiederherstellung.md").read_text(encoding="utf-8")

    for part in (
        "docker compose stop",
        "/data/backups",
        "docker compose up -d",
        "premigration",
        "app.cli restore",
        "calculation_files",
    ):
        assert part in guide


# --- Issue #195: the stored calculation files belong to the backup -----------------------

STORED = "{:032x}.gcode.3mf"


def files_dir() -> Path:
    return settings().data_dir / "calculation_files"


def put_file(number: int, content: bytes | None = None) -> Path:
    folder = files_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / STORED.format(number)
    path.write_bytes(content or f"file {number}".encode())
    return path


def put_pending(token: str = "a" * 32) -> Path:
    pending = files_dir() / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / token).write_bytes(b"waiting")
    return pending / token


def archive_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def test_195_1_the_backup_holds_the_database_and_every_stored_file() -> None:
    make_database()
    first, second = put_file(1), put_file(2, b"other")

    path = backup.make_backup(settings(), Kind.MANUAL, at(2026, 11, 13))

    assert path.suffix == ".zip"
    assert archive_names(path) == {
        "makerhub3d.db",
        f"calculation_files/{first.name}",
        f"calculation_files/{second.name}",
    }
    with zipfile.ZipFile(path) as archive:
        assert archive.read(f"calculation_files/{second.name}") == b"other"


def test_195_2_without_stored_files_the_backup_still_works() -> None:
    make_database()

    path = backup.make_backup(settings(), Kind.MANUAL, at(2026, 11, 13))

    assert archive_names(path) == {"makerhub3d.db"}


def test_195_3_waiting_uploads_and_foreign_files_are_not_in_the_backup() -> None:
    make_database()
    stored = put_file(1)
    put_pending()
    (files_dir() / "notes.txt").write_text("x", encoding="utf-8")

    path = backup.make_backup(settings(), Kind.MANUAL, at(2026, 11, 13))

    assert archive_names(path) == {"makerhub3d.db", f"calculation_files/{stored.name}"}


def test_195_4_nightly_and_pre_migration_backups_hold_the_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_database()
    stored = put_file(1)

    nightly = backup.run_nightly(settings(), at(2026, 11, 13))
    monkeypatch.setattr("app.db.head_revision", lambda: "9999")
    with AppClient(create_app()):
        pass

    [before] = [
        i for i in backup.list_backups(settings().backup_dir) if i.kind == Kind.PREMIGRATION
    ]
    for path in (nightly, settings().backup_dir / before.name):
        assert f"calculation_files/{stored.name}" in archive_names(path)


def test_195_5_the_download_is_the_archive_with_the_files(client: AppClient) -> None:
    stored = put_file(1)
    client.post("/settings/backups", data={})
    [item] = backup.list_backups(settings().backup_dir)

    download = client.get(f"/settings/backups/{item.name}/download")

    assert download.status_code == 200
    copy = settings().data_dir / "downloaded.zip"
    copy.write_bytes(download.content)
    assert f"calculation_files/{stored.name}" in archive_names(copy)


def test_195_6_no_leftover_after_a_failure_and_the_archive_is_private() -> None:
    make_database()
    put_file(1)
    path = backup.make_backup(settings(), Kind.MANUAL, at(2026, 11, 13))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(settings().backup_dir.glob(".tmp-*")) == []


def test_195_7_an_unreadable_file_fails_the_backup_without_a_leftover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_database()
    broken = put_file(1)
    original = zipfile.ZipFile.write

    def write(self, filename, *args, **kwargs):
        if Path(filename) == broken:
            raise PermissionError
        return original(self, filename, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", write)

    with pytest.raises(BackupError):
        backup.make_backup(settings(), Kind.MANUAL, at(2026, 11, 13))

    assert backup.list_backups(settings().backup_dir) == []
    assert list(settings().backup_dir.glob(".tmp-*")) == []


def test_195_7_a_failed_backup_before_a_migration_stops_the_start_also_for_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_database()
    put_file(1)

    def fail(*args, **kwargs):
        raise BackupError("x")

    monkeypatch.setattr("app.backup.create_backup", fail)
    monkeypatch.setattr("app.db.head_revision", lambda: "9999")

    with pytest.raises(BackupError), AppClient(create_app()):
        pass


def test_195_8_old_db_backups_are_listed_kept_by_the_same_rules_and_foreign_files_stay() -> None:
    make_database()
    folder = settings().backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    for day in range(1, 21):  # 20 old-style backups plus 20 new ones, on the same days
        (folder / f"makerhub3d-202610{day:02d}-030000-daily.db").write_bytes(b"old")
    for back in range(20):
        put_backup(Kind.DAILY, at(2026, 11, 20) - timedelta(days=back))
    (folder / "notes.txt").write_text("keep", encoding="utf-8")

    backup.prune(folder, BERLIN)

    kept = backup.list_backups(folder)
    assert any(item.name.endswith(".zip") for item in kept)
    # old and new backups count together: the newest 14 days stay, all of them from November
    november = {item.name for item in kept if item.name.startswith("makerhub3d-202611")}
    assert len(november) == backup.DAYS_KEPT
    assert not (
        folder / "makerhub3d-20261001-030000-daily.db"
    ).exists()  # beyond 14 days, not month end
    assert (folder / "makerhub3d-20261020-030000-daily.db").exists()  # last backup of October
    assert (folder / "notes.txt").exists()


def test_195_8_an_old_db_backup_is_listed_with_its_kind_and_size(client: AppClient) -> None:
    folder = settings().backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "makerhub3d-20261001-030000-daily.db").write_bytes(b"x" * 2048)
    put_backup(Kind.MANUAL, at(2026, 11, 13))

    page = client.get("/settings/backups").text

    assert "makerhub3d-20261001-030000-daily.db" in page
    assert "makerhub3d-20261113-030000-manual.zip" in page
    assert "2 kB" in page
    assert (
        client.get("/settings/backups/makerhub3d-20261001-030000-daily.db/download").status_code
        == 200
    )


def test_195_9_restoring_brings_database_and_files_back() -> None:
    database = make_database()
    add_user("vorher", "admin")
    kept, removed_later = put_file(1, b"kept"), put_file(2, b"gone later")
    saved = backup.make_backup(settings(), Kind.MANUAL, at(2026, 11, 13))
    add_user("nachher", "admin")
    kept.write_bytes(b"changed")
    removed_later.unlink()
    added_later = put_file(3)
    waiting = put_pending()

    assert cli.main(["restore", saved.name]) == 0

    assert kept.read_bytes() == b"kept"
    assert removed_later.read_bytes() == b"gone later"
    assert not added_later.exists()
    assert waiting.exists()
    assert usernames(database) == {"vorher"}
    with AppClient(create_app()) as restarted:
        assert log_in(restarted, "vorher", OTHER_PASSWORD).status_code == 200


def test_195_10_restoring_an_old_db_backup_keeps_the_files() -> None:
    database = make_database()
    add_user("vorher", "admin")
    folder = settings().backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    old = folder / "makerhub3d-20261001-030000-manual.db"
    shutil.copyfile(database, old)
    add_user("nachher", "admin")
    stored = put_file(1)

    assert cli.main(["restore", old.name]) == 0

    assert usernames(database) == {"vorher"}
    assert stored.exists()


def _evil_archive(entry: str) -> str:
    database = make_database()
    put_file(1)
    folder = settings().backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    name = "makerhub3d-20261113-030000-manual.zip"
    with zipfile.ZipFile(folder / name, "w") as archive:
        archive.write(database, "makerhub3d.db")
        archive.writestr(entry, "evil")
    return name


@pytest.mark.parametrize(
    "entry",
    [
        "calculation_files/../evil.txt",
        "../evil.txt",
        "/evil.txt",
        "calculation_files/notes.txt",
        "calculation_files/pending/" + "a" * 32,
        "other/" + "0" * 32 + ".gcode.3mf",
    ],
)
def test_195_11_an_archive_with_unexpected_entries_changes_nothing(entry: str) -> None:
    name = _evil_archive(entry)
    add_user("nachher", "admin")
    stored = files_dir() / STORED.format(1)
    before = stored.read_bytes()

    assert cli.main(["restore", name]) != 0

    assert usernames(settings().database_path) == {"nachher"}
    assert stored.read_bytes() == before
    assert not (settings().data_dir.parent / "evil.txt").exists()
    assert list(settings().data_dir.glob(".restore-*")) == []


def test_195_11_an_unknown_or_dangerous_name_changes_nothing() -> None:
    make_database()
    add_user("nachher", "admin")

    for name in ("../makerhub3d.db", "notes.txt", "makerhub3d-20990101-000000-daily.zip"):
        assert cli.main(["restore", name]) != 0
    assert usernames(settings().database_path) == {"nachher"}


def test_195_12_the_plan_no_longer_says_the_files_are_not_backed_up() -> None:
    plan = (ROOT / "docs" / "plan.md").read_text(encoding="utf-8")

    assert "werden noch nicht gesichert" not in plan


def test_195_13_restored_files_are_private() -> None:
    database = make_database()
    stored = put_file(1)
    saved = backup.make_backup(settings(), Kind.MANUAL, at(2026, 11, 13))
    os.umask(0o022)

    assert cli.main(["restore", saved.name]) == 0

    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600


def test_195_13_a_broken_archive_or_database_changes_nothing() -> None:
    make_database()
    add_user("nachher", "admin")
    folder = settings().backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "makerhub3d-20261113-030000-manual.zip").write_bytes(b"no zip")
    with zipfile.ZipFile(folder / "makerhub3d-20261113-040000-manual.zip", "w") as archive:
        archive.writestr("makerhub3d.db", "not a database")
    (folder / "makerhub3d-20261001-030000-manual.db").write_bytes(b"not a database")

    for name in sorted(p.name for p in folder.iterdir()):
        assert cli.main(["restore", name]) != 0

    assert usernames(settings().database_path) == {"nachher"}
    assert list(settings().data_dir.glob(".restore-*")) == []


def test_195_13_a_failure_while_replacing_files_is_reported_and_keeps_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_database()
    add_user("vorher", "admin")
    put_file(1)
    saved = backup.make_backup(settings(), Kind.MANUAL, at(2026, 11, 13))
    add_user("nachher", "admin")

    def fail(*args, **kwargs):
        raise PermissionError

    monkeypatch.setattr("app.backup.os.replace", fail)

    assert cli.main(["restore", saved.name]) == backup_exit_code()
    assert usernames(settings().database_path) == {"vorher", "nachher"}


def backup_exit_code() -> int:
    return cli.RESTORE_FAILED
