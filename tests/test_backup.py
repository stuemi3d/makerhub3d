"""Database backup (issue #9, test cases 1-17)."""

import json
import os
import shutil
import sqlite3
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app import backup
from app.backup import BackupError, Kind
from app.config import Settings
from app.db import DATABASE_FILE, make_engine, run_migrations
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


def usernames(database: Path) -> set[str]:
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
        copy = sqlite3.connect(path)
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
        f"makerhub3d-202611{day:02d}-120000-premigration.db" for day in range(4, 9)
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

    name = "makerhub3d-20261112-030000-daily.db"
    download = client.get(f"/settings/backups/{name}/download")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    copy = settings().data_dir / "downloaded.db"
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
        "/settings/backups/makerhub3d-20261112-030000-daily.db/download", follow_redirects=False
    )
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


@pytest.mark.parametrize(
    "name",
    [
        "..%2Fmakerhub3d.db",
        "..%2F..%2Fetc%2Fpasswd",
        "makerhub3d-20990101-000000-daily.db",  # fits the pattern but does not exist
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

    # The steps of the guide: app stopped (no engine open here), copy the file back.
    shutil.copyfile(saved, database)
    for leftover in (
        database.with_name(DATABASE_FILE + "-wal"),
        database.with_name(DATABASE_FILE + "-shm"),
    ):
        leftover.unlink(missing_ok=True)

    with AppClient(create_app()) as restarted:
        assert log_in(restarted, "vorher", OTHER_PASSWORD).status_code == 200
        assert restarted.get("/").status_code == 200
    assert usernames(database) == {"vorher"}


def test_the_guide_names_the_steps() -> None:
    guide = (ROOT / "docs" / "wiederherstellung.md").read_text(encoding="utf-8")

    for part in ("docker compose stop", "/data/backups", "docker compose up -d", "premigration"):
        assert part in guide
