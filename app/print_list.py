"""The prints of Bambuddy as a list for the page Production and the dashboard (issue #119).

Read-only. Only prints from the start date of the Bambuddy settings on are read; the older
history is neither read nor shown. Which print is open, in a production or ignored is
decided here from MakerHub's own tables.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import list_filter, production, table_sort
from app.bambuddy import Archive, BambuddyClient, BambuddyError
from app.list_filter import Field
from app.models import BambuddyConnection, Device
from app.settings_form import format_decimal
from app.two_factor import Cipher

# Bambuddy statuses that count as a failed print (shown in red)
FAILED_STATUSES = frozenset({"failed", "cancelled"})
VIEWS = ("open", "assigned", "ignored")
SORT_KEYS = ("date", "printer", "name")
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
    # What Bambuddy knows about the print, stored with the production (issue #148)
    grams: Decimal | None = None
    kwh: Decimal | None = None
    filament_type: str | None = None
    filament_color: str | None = None
    # The Bambuddy printer the print ran on (for the machine rate of a production, issue #162)
    printer_id: int | None = None
    # When the print ended (to find its lines in Bambuddy's usage history, issue #228)
    finished_at: datetime | None = None
    # Name of the printer shown in the list (issue #275); None = not known
    printer_name: str | None = None
    # Result articles of its active production (for the filter, issue #276)
    article_ids: tuple[int, ...] = ()


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


@dataclass(frozen=True)
class _Known:
    """What MakerHub itself knows about the prints."""

    assigned: dict[int, int]
    ignored: set[int]
    # Archive id -> printer stored with its active production
    stored_printers: dict[int, str]
    # Bambuddy printer id -> name of the linked printer device
    linked_printers: dict[int, str]
    # Archive id -> result articles of its active production
    articles: dict[int, tuple[int, ...]]


def linked_printer_names(db: Session) -> dict[int, str]:
    """Bambuddy printer id -> name of the printer device linked to it, switched off or not."""
    found = db.execute(
        select(Device.bambuddy_printer_id, Device.name).where(
            Device.kind == "printer", Device.bambuddy_printer_id.is_not(None)
        )
    )
    return {printer_id: name for printer_id, name in found}


def _row(archive: Archive, language: str, known: _Known) -> PrintRow:
    assigned, ignored = known.assigned, known.ignored
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
        grams=archive.filament_grams,
        kwh=archive.energy_kwh,
        filament_type=archive.filament_type,
        filament_color=archive.filament_color,
        printer_id=archive.printer_id,
        finished_at=archive.completed_at,
        # The printer booked with the production first, else the one linked in MakerHub
        printer_name=(known.stored_printers.get(archive.id) if state == "assigned" else None)
        or (None if archive.printer_id is None else known.linked_printers.get(archive.printer_id)),
        article_ids=known.articles.get(archive.id, ()) if state == "assigned" else (),
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
        known = _Known(
            assigned=production.assigned_productions(db),
            ignored=production.ignored_archive_ids(db),
            stored_printers=production.assigned_printer_names(db),
            linked_printers=linked_printer_names(db),
            articles=production.assigned_articles(db),
        )
    extra = {} if timeout is None else {"timeout": timeout}
    try:
        # A short wait counts for the whole reading, not only for one page
        archives, truncated = BambuddyClient(
            url, api_key, transport=transport, **extra
        ).archives_since(since, budget=timeout)
    except BambuddyError as error:
        return PrintList([], error=(f"bambuddy.error.{error.code}", error.path))
    return PrintList([_row(archive, language, known) for archive in archives], truncated=truncated)


def in_view(rows: list[PrintRow], view: str) -> list[PrintRow]:
    return [row for row in rows if row.state == view]


def filter_fields(
    rows: list[PrintRow], view: str, articles: list[tuple[int, str]]
) -> tuple[Field, ...]:
    """The filter bar of one view (issue #276): the printer choices are those shown in its rows;
    only the view "assigned" has the article, with the given (id, label) choices."""
    printers = sorted({row.printer_name for row in rows if row.printer_name}, key=str.casefold)
    fields = (
        Field("from", "production.filter.from", kind="date"),
        Field("to", "production.filter.to", kind="date"),
        Field(
            "printer",
            "production.filter.printer",
            (("", "production.filter.printer.all"), *((n, n) for n in printers)),
        ),
        Field("q", "filter.search"),
    )
    if view != "assigned":
        return fields
    article = Field(
        "article",
        "production.filter.article",
        (("", "production.filter.article.all"), *((str(i), label) for i, label in articles)),
    )
    return (*fields, article)


def keeps(row: PrintRow, values: dict[str, str]) -> bool:
    """Whether a print passes the filter; the day is the one shown in the list."""
    first, last = list_filter.date_of(values["from"]), list_filter.date_of(values["to"])
    day = None if row.sort_time == datetime.min else row.sort_time.date()
    return (
        (first is None or (day is not None and day >= first))
        and (last is None or (day is not None and day <= last))
        and (not values["printer"] or values["printer"] == row.printer_name)
        and list_filter.matches(values["q"], row.name)
        and (not values.get("article") or int(values["article"]) in row.article_ids)
    )


# What the way back after ignoring may carry: the filter of every view and the sorting
BACK_FILTER = ("from", "to", "printer", "q", "article")


def way_back(view: str, raw: dict[str, str]) -> str:
    """The address of the list after ignoring or bringing back (issue #276): the same view,
    filter and sorting. Only known names; dates and the sorting are checked, texts are cut, so
    the address always stays on this page."""
    kept = {"view": view if view in VIEWS else "open"}
    for name in BACK_FILTER:
        value = (raw.get(name) or "").strip()[: list_filter.MAX_TEXT]
        if name in ("from", "to") and list_filter.date_of(value) is None:
            continue
        if name == "article" and not re.fullmatch(r"[0-9]{1,9}", value):
            continue
        if value:
            kept[name] = value
    chosen = table_sort.chosen_sort(SORT_KEYS, raw.get("sort"), raw.get("dir"))
    if chosen:
        kept.update(sort=chosen[0], dir=chosen[1])
    return "/production?" + urlencode(kept)


def arranged(
    rows: list[PrintRow],
    view: str,
    path: str,
    sort: str | None,
    direction: str | None,
    keep: dict[str, str] | None = None,
) -> tuple[list[PrintRow], dict[str, dict[str, str]]]:
    """The rows of one view in the order asked for, and the header links of the table; `keep`
    are the filter values the links carry along."""
    chosen = table_sort.chosen_sort(SORT_KEYS, sort, direction)
    key, order = chosen or DEFAULT_SORT
    shown = in_view(rows, view)
    # Newest first as the base order, so equal values keep it
    shown.sort(key=lambda row: (row.sort_time, row.archive_id), reverse=True)
    column = {
        "date": lambda row: row.sort_time,
        "printer": lambda row: (row.printer_name or "").casefold(),
        "name": lambda row: row.name.casefold(),
    }[key]
    shown.sort(key=column, reverse=order == "desc")
    links = table_sort.sort_links(
        path, SORT_KEYS, chosen, DEFAULT_SORT, {"view": view, **(keep or {})}
    )
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
