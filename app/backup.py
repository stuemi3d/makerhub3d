"""Database backups (issue #9): nightly, before a migration and by hand.

A backup is made with the SQLite backup function, which gives a consistent copy even while
the app writes (copying the file could catch it halfway through a write). Files are only
readable by the app's user: they contain password hashes and encrypted two-factor secrets.

Clean-up (see `prune`):
- daily and manual backups: everything of the newest 14 days that have a backup, plus the
  last backup of each of the newest 12 months
- backups before a migration: the newest 5
Only files that follow the naming pattern are ever listed or deleted.
"""

import asyncio
import contextlib
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings
from app.i18n import translate

NIGHTLY_HOUR = 3
DAYS_KEPT = 14
MONTHS_KEPT = 12
PRE_MIGRATION_KEPT = 5

_NAME = re.compile(r"makerhub3d-(\d{8})-(\d{6})-(daily|manual|premigration)\.db")


class Kind(StrEnum):
    DAILY = "daily"
    MANUAL = "manual"
    PREMIGRATION = "premigration"


class BackupError(RuntimeError):
    """A backup could not be made. The message is generic; details stay out of the log."""


@dataclass(frozen=True)
class BackupFile:
    name: str
    kind: Kind
    created_at: datetime  # UTC
    size: int


def _parse(path: Path) -> BackupFile | None:
    match = _NAME.fullmatch(path.name)
    if match is None or path.is_symlink() or not path.is_file():
        return None
    try:
        created = datetime.strptime(match[1] + match[2], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        size = path.stat().st_size
    except (ValueError, OSError):
        return None
    return BackupFile(path.name, Kind(match[3]), created, size)


def list_backups(backup_dir: Path) -> list[BackupFile]:
    """All backups, newest first. A missing or unreadable folder gives an empty list."""
    try:
        paths = list(backup_dir.iterdir())
    except OSError:
        return []
    found = [item for path in paths if (item := _parse(path)) is not None]
    return sorted(found, key=lambda item: item.name[len("makerhub3d-") :], reverse=True)


def find_backup(backup_dir: Path, name: str) -> Path | None:
    """The file of a backup by its name; only names of listed backups are accepted."""
    if _NAME.fullmatch(name) is None:
        return None
    path = backup_dir / name
    return path if _parse(path) is not None else None


def create_backup(
    database: Path, backup_dir: Path, kind: Kind, now: datetime | None = None
) -> Path:
    """Writes a consistent copy of the database into the backup folder."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    name = f"makerhub3d-{moment:%Y%m%d-%H%M%S}-{kind}.db"
    temporary: str | None = None
    try:
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(backup_dir, 0o700)
        # The copy is built under a temporary name, so a crash never leaves a broken backup
        # that looks like a real one. mkstemp creates the file readable for the owner only.
        handle, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=backup_dir)
        os.close(handle)
        source = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise BackupError("The backup did not pass the integrity check.")
        finally:
            target.close()
            source.close()
        final = backup_dir / name
        os.replace(temporary, final)
        temporary = None
        os.chmod(final, 0o600)
        return final
    except (OSError, sqlite3.Error) as error:
        raise BackupError("The backup could not be made.") from error
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


def _remove_stale_partial_files(backup_dir: Path) -> None:
    """Deletes leftovers of a backup that was interrupted (crash) more than an hour ago."""
    limit = datetime.now(UTC).timestamp() - 3600
    try:
        for path in backup_dir.glob(".tmp-*.part"):
            with contextlib.suppress(OSError):
                if not path.is_symlink() and path.stat().st_mtime < limit:
                    path.unlink()
    except OSError:
        pass


def prune(backup_dir: Path, zone: ZoneInfo) -> list[str]:
    """Deletes backups that are no longer needed; returns their names."""
    items = list_backups(backup_dir)
    regular = [item for item in items if item.kind != Kind.PREMIGRATION]
    before_migration = [item for item in items if item.kind == Kind.PREMIGRATION]

    def local(item: BackupFile) -> datetime:
        return item.created_at.astimezone(zone)

    keep = {item.name for item in before_migration[:PRE_MIGRATION_KEPT]}
    days = sorted({local(item).date() for item in regular}, reverse=True)[:DAYS_KEPT]
    keep |= {item.name for item in regular if local(item).date() in days}
    newest_of_month: dict[tuple[int, int], BackupFile] = {}
    for item in regular:  # newest first, so the first one seen is the last of its month
        newest_of_month.setdefault((local(item).year, local(item).month), item)
    for month in sorted(newest_of_month, reverse=True)[:MONTHS_KEPT]:
        keep.add(newest_of_month[month].name)

    removed = []
    _remove_stale_partial_files(backup_dir)
    for item in items:
        if item.name not in keep:
            try:
                (backup_dir / item.name).unlink()
            except OSError:
                continue
            removed.append(item.name)
    return removed


def _log_failure(settings: Settings, key: str, error: BaseException) -> None:
    # Only the kind of error, never paths or database content.
    cause = type(error.__cause__ or error).__name__
    message = translate(settings.language, key).format(reason=cause)
    print(message, file=sys.stderr, flush=True)


def make_backup(settings: Settings, kind: Kind, now: datetime | None = None) -> Path:
    """Creates a backup and cleans up afterwards."""
    path = create_backup(settings.database_path, settings.backup_dir, kind, now)
    prune(settings.backup_dir, ZoneInfo(settings.timezone))
    return path


def backup_before_migration(settings: Settings) -> None:
    """Called before a migration. If it fails, the app must not migrate (and so not start)."""
    try:
        make_backup(settings, Kind.PREMIGRATION)
    except BackupError as error:
        _log_failure(settings, "backup.log_pre_migration_failed", error)
        # No cause chain: it would put the file path into the start-up traceback.
        raise BackupError("The backup before the migration could not be made.") from None


def run_nightly(settings: Settings, now: datetime | None = None) -> Path | None:
    """The nightly backup; does nothing if today already has one. Never raises."""
    zone = ZoneInfo(settings.timezone)
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    today = moment.astimezone(zone).date()
    if any(
        item.kind == Kind.DAILY and item.created_at.astimezone(zone).date() == today
        for item in list_backups(settings.backup_dir)
    ):
        return None
    try:
        return make_backup(settings, Kind.DAILY, moment)
    except BackupError as error:
        _log_failure(settings, "backup.log_nightly_failed", error)
        return None


def seconds_until_next_run(now: datetime, zone: ZoneInfo) -> float:
    local = now.astimezone(zone)
    target = datetime.combine(local.date(), datetime.min.time(), zone).replace(hour=NIGHTLY_HOUR)
    if local >= target:
        target = datetime.combine(
            local.date() + timedelta(days=1), datetime.min.time(), zone
        ).replace(hour=NIGHTLY_HOUR)
    return (target.astimezone(UTC) - local.astimezone(UTC)).total_seconds()


async def nightly_loop(settings: Settings) -> None:
    """Runs until cancelled: waits for 03:00 local time, makes the backup, repeats."""
    zone = ZoneInfo(settings.timezone)
    while True:
        await asyncio.sleep(seconds_until_next_run(datetime.now(UTC), zone))
        try:
            await asyncio.to_thread(run_nightly, settings)
        except Exception as error:  # one bad night must not stop all later ones
            _log_failure(settings, "backup.log_nightly_failed", error)
