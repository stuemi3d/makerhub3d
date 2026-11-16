"""The prints of Bambuddy as a list for the page Production and the dashboard (issue #119).

Read-only. Only prints from the start date of the Bambuddy settings on are read; the older
history is neither read nor shown. Which print is open, in a production or ignored is
decided here from MakerHub's own tables.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import httpx2
from sqlalchemy.orm import Session

from app import production, table_sort
from app.bambuddy import Archive, BambuddyClient, BambuddyError
from app.models import BambuddyConnection
from app.settings_form import format_decimal
from app.two_factor import Cipher

# Bambuddy statuses that count as a failed print (shown in red)
FAILED_STATUSES = frozenset({"failed", "cancelled"})
VIEWS = ("open", "assigned", "ignored")
SORT_KEYS = ("date", "name")
DEFAULT_SORT = ("date", "desc")
DASHBOARD_TIMEOUT = 3.0
DASHBOARD_ROWS = 5


@dataclass(frozen=True)
class PrintRow:
    archive_id: int
    name: str
    when: str
    sort_time: datetime
    duration: str | None
    status: str
    failed: bool
    material_cost: str | None
    electricity_cost: str | None
    state: str
    production_id: int | None
    # The same costs as numbers (for the form of a production)
    material_value: Decimal | None = None
    electricity_value: Decimal | None = None
    # Print time in seconds (for the machine costs of a production)
    seconds: int | None = None


@dataclass(frozen=True)
class PrintList:
    rows: list[PrintRow]
    # No address or key saved yet
    not_set_up: bool = False
    # (translation key, Bambuddy path) of a problem reading the archive
    error: tuple[str, str] | None = None
    # The upper limit cut the list
    truncated: bool = False


def format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    minutes = seconds // 60
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if hours else f"{rest} min"


def _money(value: Decimal | None, language: str) -> str | None:
    return None if value is None else format_decimal(value, language)


def _row(archive: Archive, language: str, assigned: dict[int, int], ignored: set[int]) -> PrintRow:
    moment = archive.started_at or archive.created_at or archive.completed_at
    if archive.id in assigned:
        state = "assigned"
    elif archive.id in ignored:
        state = "ignored"
    else:
        state = "open"
    shown = ""
    if moment is not None:
        pattern = "%d.%m.%Y %H:%M" if language == "de" else "%Y-%m-%d %H:%M"
        shown = moment.strftime(pattern)
    return PrintRow(
        archive_id=archive.id,
        name=archive.name,
        when=shown,
        sort_time=moment or datetime.min,
        duration=format_duration(archive.print_seconds),
        status=archive.status,
        failed=archive.status in FAILED_STATUSES,
        material_cost=_money(archive.material_cost, language),
        electricity_cost=_money(archive.energy_cost, language),
        state=state,
        production_id=assigned.get(archive.id),
        material_value=archive.material_cost,
        electricity_value=archive.energy_cost,
        seconds=archive.print_seconds,
    )


def load(
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    transport: httpx2.BaseTransport | None,
    language: str,
    timeout: float | None = None,
) -> PrintList:
    """Reads the prints since the start date. Problems come back as data, never as an
    exception, so that pages stay usable."""
    with new_session() as db:
        row = db.get(BambuddyConnection, 1)
        url, token, since = row.url, row.api_key_encrypted, row.prints_from
        if not url or not token or since is None:
            return PrintList([], not_set_up=True)
        api_key = cipher.decrypt(token) if cipher is not None else None
        if api_key is None:
            return PrintList([], error=("bambuddy.error.key_unreadable", ""))
        assigned = production.assigned_productions(db)
        ignored = production.ignored_archive_ids(db)
    extra = {} if timeout is None else {"timeout": timeout}
    try:
        # A short wait counts for the whole reading, not only for one page
        archives, truncated = BambuddyClient(
            url, api_key, transport=transport, **extra
        ).archives_since(since, budget=timeout)
    except BambuddyError as error:
        return PrintList([], error=(f"bambuddy.error.{error.code}", error.path))
    return PrintList(
        [_row(archive, language, assigned, ignored) for archive in archives], truncated=truncated
    )


def arranged(
    rows: list[PrintRow],
    view: str,
    path: str,
    sort: str | None,
    direction: str | None,
) -> tuple[list[PrintRow], dict[str, dict[str, str]]]:
    """The rows of one view in the order asked for, and the header links of the table."""
    chosen = table_sort.chosen_sort(SORT_KEYS, sort, direction)
    key, order = chosen or DEFAULT_SORT
    shown = [row for row in rows if row.state == view]
    # Newest first as the base order, so equal values keep it
    shown.sort(key=lambda row: (row.sort_time, row.archive_id), reverse=True)
    column = {"date": lambda row: row.sort_time, "name": lambda row: row.name.casefold()}[key]
    shown.sort(key=column, reverse=order == "desc")
    links = table_sort.sort_links(path, SORT_KEYS, chosen, DEFAULT_SORT, {"view": view})
    return shown, links


@dataclass(frozen=True)
class OpenSummary:
    """For the dashboard: how many prints are open and the latest few."""

    count: int
    latest: list[PrintRow]
    # "ok", "not_set_up" or "unavailable"
    state: str


def open_summary(prints: PrintList) -> OpenSummary:
    if prints.not_set_up:
        return OpenSummary(0, [], "not_set_up")
    if prints.error:
        return OpenSummary(0, [], "unavailable")
    open_rows = [row for row in prints.rows if row.state == "open"]
    open_rows.sort(key=lambda row: (row.sort_time, row.archive_id), reverse=True)
    return OpenSummary(len(open_rows), open_rows[:DASHBOARD_ROWS], "ok")
