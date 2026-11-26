"""Productions as lists and as a detail page (issues #127, #140). Read-only."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import print_list, production, stock, table_sort
from app.models import Article, PrintStatus, Production
from app.production_form import input_text, money_text

SORT_KEYS = ("date", "printer", "cost")
DEFAULT_SORT = ("date", "desc")


@dataclass(frozen=True)
class ListRow:
    id: int
    date: str
    booked_on: date
    created_at: datetime
    note: str
    print_count: int
    articles: str
    cost: Decimal
    cost_text: str
    cancelled: bool
    # Names of the printers of its prints (each once, in the order of the prints; issue #259)
    printers: tuple[str, ...] = ()
    # Ids of the result articles
    article_ids: tuple[int, ...] = ()


def list_rows(db: Session, language: str) -> list[ListRow]:
    numbers = {
        article_id: number for article_id, number in db.execute(select(Article.id, Article.number))
    }
    rows = []
    for item in db.scalars(select(Production).order_by(Production.id.desc())):
        rows.append(
            ListRow(
                id=item.id,
                date=stock.format_date(item.booked_on, language),
                booked_on=item.booked_on,
                created_at=item.created_at,
                note=item.note,
                print_count=len(item.prints),
                articles=", ".join(f"{numbers[r.article_id]} x {r.quantity}" for r in item.results),
                cost=item.total_cost,
                cost_text=money_text(item.total_cost, language),
                cancelled=item.cancelled,
                printers=tuple(
                    dict.fromkeys(p.printer_name for p in item.prints if p.printer_name)
                ),
                article_ids=tuple(r.article_id for r in item.results),
            )
        )
    return rows


def arranged(
    rows: list[ListRow],
    sort: str | None,
    direction: str | None,
    keep: dict[str, str] | None = None,
) -> tuple[list[ListRow], dict[str, dict[str, str]]]:
    """The rows in the order asked for in the address (default: newest first) and the header
    links of the table."""
    chosen = table_sort.chosen_sort(SORT_KEYS, sort, direction)
    key, order = chosen or DEFAULT_SORT
    shown = sorted(rows, key=lambda row: (row.booked_on, row.created_at, row.id), reverse=True)
    column = {
        "date": lambda row: (row.booked_on, row.created_at),
        "printer": lambda row: ", ".join(row.printers).casefold(),
        "cost": lambda row: row.cost,
    }
    shown = sorted(shown, key=column[key], reverse=order == "desc")
    links = table_sort.sort_links("/production/list", SORT_KEYS, chosen, DEFAULT_SORT, keep)
    return shown, links


def _number(value: Decimal | int | None, language: str) -> str | None:
    """An amount without trailing zeros, or None when unknown."""
    return None if value is None else input_text(Decimal(value), language)


@dataclass(frozen=True)
class DetailPrint:
    archive_id: int
    failed: bool
    material: str
    electricity: str
    machine: str
    # Only set for productions booked before issue #135 (wear taken from Bambuddy)
    wear: str | None
    total: str
    # Amounts as stored (issue #148); None = unknown
    duration: str | None
    grams: str | None
    kwh: str | None
    filament_type: str | None
    colors: list[str]


@dataclass(frozen=True)
class DetailResult:
    article_id: int
    number: str
    name: str
    quantity: int
    cost: str
    per_piece: str


@dataclass(frozen=True)
class DetailLabor:
    minutes: str
    rate: str
    cost: str


@dataclass(frozen=True)
class Detail:
    id: int
    date: str
    note: str
    total: str
    # Only set when a working time was booked
    labor: "DetailLabor | None"
    # Whether any print has an old wear value (then the column is shown)
    has_wear: bool
    cancelled: bool
    failed_pieces: int
    # Sums over the prints that know the value; None when no print does
    total_duration: str | None
    total_grams: str | None
    total_kwh: str | None
    prints: list[DetailPrint]
    results: list[DetailResult]


def _sum(values: list[Decimal | int | None]) -> Decimal | int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def detail(db: Session, production_id: int, language: str) -> Detail | None:
    item = db.get(Production, production_id)
    if item is None:
        return None
    articles = {a.id: a for a in db.scalars(select(Article))}
    return Detail(
        id=item.id,
        date=stock.format_date(item.booked_on, language),
        note=item.note,
        total=money_text(item.total_cost, language),
        labor=DetailLabor(
            minutes=input_text(item.labor_minutes, language),
            rate=money_text(item.labor_rate, language),
            cost=money_text(item.labor_cost, language),
        )
        if item.labor_minutes > 0
        else None,
        has_wear=any(p.wear_cost > 0 for p in item.prints),
        cancelled=item.cancelled,
        failed_pieces=item.failed_pieces,
        total_duration=print_list.format_duration(_sum([p.print_seconds for p in item.prints])),
        total_grams=_number(_sum([p.filament_grams for p in item.prints]), language),
        total_kwh=_number(_sum([p.energy_kwh for p in item.prints]), language),
        prints=[
            DetailPrint(
                archive_id=p.archive_id,
                failed=p.status == PrintStatus.FAILED,
                material=money_text(p.material_cost, language),
                electricity=money_text(p.electricity_cost, language),
                machine=money_text(p.machine_cost, language),
                wear=money_text(p.wear_cost, language) if p.wear_cost > 0 else None,
                total=money_text(
                    p.material_cost + p.electricity_cost + p.machine_cost + p.wear_cost, language
                ),
                duration=print_list.format_duration(p.print_seconds),
                grams=_number(p.filament_grams, language),
                kwh=_number(p.energy_kwh, language),
                filament_type=production.clean_type(p.filament_type),
                colors=production.color_list(p.filament_colors),
            )
            for p in item.prints
        ],
        results=[
            DetailResult(
                article_id=r.article_id,
                number=articles[r.article_id].number,
                name=articles[r.article_id].name,
                quantity=r.quantity,
                cost=money_text(r.allocated_cost, language),
                per_piece=money_text(r.allocated_cost / r.quantity, language),
            )
            for r in item.results
        ],
    )
