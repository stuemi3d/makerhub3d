"""Productions as lists and as a detail page (issue #127). Read-only."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import stock, table_sort
from app.models import Article, PrintStatus, Production
from app.production_form import money_text

SORT_KEYS = ("date", "cost")
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
            )
        )
    return rows


def arranged(
    rows: list[ListRow], sort: str | None, direction: str | None
) -> tuple[list[ListRow], dict[str, dict[str, str]]]:
    """The rows in the order asked for in the address (default: newest first) and the header
    links of the table."""
    chosen = table_sort.chosen_sort(SORT_KEYS, sort, direction)
    key, order = chosen or DEFAULT_SORT
    shown = sorted(rows, key=lambda row: (row.booked_on, row.created_at, row.id), reverse=True)
    column = {"date": lambda row: (row.booked_on, row.created_at), "cost": lambda row: row.cost}
    shown = sorted(shown, key=column[key], reverse=order == "desc")
    links = table_sort.sort_links("/production/list", SORT_KEYS, chosen, DEFAULT_SORT)
    return shown, links


@dataclass(frozen=True)
class DetailPrint:
    archive_id: int
    failed: bool
    material: str
    electricity: str
    wear: str
    total: str


@dataclass(frozen=True)
class DetailResult:
    article_id: int
    number: str
    name: str
    quantity: int
    weight: str
    cost: str
    per_piece: str
    split: bool


@dataclass(frozen=True)
class Detail:
    id: int
    date: str
    note: str
    total: str
    cancelled: bool
    prints: list[DetailPrint]
    results: list[DetailResult]


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
        cancelled=item.cancelled,
        prints=[
            DetailPrint(
                archive_id=p.archive_id,
                failed=p.status == PrintStatus.FAILED,
                material=money_text(p.material_cost, language),
                electricity=money_text(p.electricity_cost, language),
                wear=money_text(p.wear_cost, language),
                total=money_text(p.material_cost + p.electricity_cost + p.wear_cost, language),
            )
            for p in item.prints
        ],
        results=[
            DetailResult(
                article_id=r.article_id,
                number=articles[r.article_id].number,
                name=articles[r.article_id].name,
                quantity=r.quantity,
                weight=money_text(r.weight, language),
                cost=money_text(r.allocated_cost, language),
                per_piece=money_text(r.allocated_cost / r.quantity, language),
                split=r.split,
            )
            for r in item.results
        ],
    )
