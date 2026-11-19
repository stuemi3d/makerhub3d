"""Analysis of everything over a period (issue #206): revenue, cost of the sold pieces, gross
profit, top and flop articles, stock value, slow sellers and the cost of failed prints.

Like the analysis per article (issue #203) everything is calculated from stored values and
cancelled productions and sales never count. Money is summed in Python as Decimal.

Cost of the sold pieces: each sale is valued with the average cost per piece of all pieces
produced up to the day of the sale (moving average). A sale is never recalculated later, and a
sale before any production has no known cost (it counts 0).
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import stock
from app.analysis import money, percent
from app.models import (
    Article,
    PrintStatus,
    Production,
    ProductionPrint,
    ProductionResult,
    Sale,
)

ZERO = Decimal(0)
PERIODS = ("month", "last_month", "year", "last_year", "custom")
DEFAULT_PERIOD = "year"
TOP_SIZE = 5
# A piece in stock that was not sold for longer than this is a slow seller
SLOW_DAYS = 90
# A free period may end at most this far after today
FUTURE_DAYS = 366
ERROR_PERIOD = "analysis_total.error.period"
_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class Selection:
    """The chosen period; `error` is a text key when a free period is not valid."""

    key: str
    start: date | None
    end: date | None
    error: str | None = None


@dataclass(frozen=True)
class Line:
    article_id: int
    number: str
    name: str
    quantity: int
    revenue: Decimal
    cost: Decimal

    @property
    def profit(self) -> Decimal:
        return self.revenue - self.cost


@dataclass(frozen=True)
class SlowRow:
    article_id: int
    number: str
    name: str
    stock: int
    last_sale: date | None


@dataclass(frozen=True)
class Totals:
    revenue: Decimal
    cost_of_sold: Decimal
    gross_profit: Decimal
    stock_value: Decimal
    failed_cost: Decimal
    # Share of the failed cost in the production cost of the period, in percent; None without
    # any production
    failed_share: Decimal | None
    top: list[Line]
    flop: list[Line]
    slow: list[SlowRow]


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _month_end(day: date) -> date:
    return (_month_start(day) + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _parse_day(text: str) -> date | None:
    """A date like 2026-03-05; anything else (also other number systems) is None."""
    text = text.strip()
    if not _ISO_DAY.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def choose(key: str | None, start_text: str, end_text: str, today: date) -> Selection:
    """The period from the address. An unknown name means the default (this year)."""
    if key not in PERIODS:
        key = DEFAULT_PERIOD
    if key == "month":
        return Selection(key, _month_start(today), _month_end(today))
    if key == "last_month":
        last = _month_start(today) - timedelta(days=1)
        return Selection(key, _month_start(last), last)
    if key == "year":
        return Selection(key, date(today.year, 1, 1), date(today.year, 12, 31))
    if key == "last_year":
        return Selection(key, date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
    start, end = _parse_day(start_text), _parse_day(end_text)
    if start is None or end is None or end < start or end > today + timedelta(days=FUTURE_DAYS):
        return Selection(key, None, None, ERROR_PERIOD)
    return Selection(key, start, end)


def _productions(db: Session) -> dict[int, list[tuple[date, int, Decimal]]]:
    """Per article the productions that are not cancelled: (day, good pieces, cost), by day."""
    query = (
        select(ProductionResult.article_id, Production.booked_on, ProductionResult.quantity)
        .add_columns(ProductionResult.allocated_cost)
        .join(Production, Production.id == ProductionResult.production_id)
        .where(Production.cancelled.is_(False))
        .order_by(Production.booked_on, Production.id)
    )
    found: dict[int, list[tuple[date, int, Decimal]]] = {}
    for article_id, day, quantity, cost in db.execute(query):
        found.setdefault(article_id, []).append((day, quantity, cost))
    return found


def _average_until(items: list[tuple[date, int, Decimal]], day: date) -> Decimal:
    """Average cost per piece of the pieces produced up to and including the day; 0 if none."""
    pieces = 0
    cost = ZERO
    for produced_on, quantity, amount in items:
        if produced_on <= day:
            pieces += quantity
            cost += amount
    return cost / pieces if pieces else ZERO


def compute(db: Session, start: date, end: date, today: date) -> Totals:
    """All figures of the period; stock value and slow sellers are of the day `today`."""
    made = _productions(db)
    articles = {a.id: a for a in db.scalars(select(Article))}

    lines: dict[int, Line] = {}
    sales_query = select(Sale).where(
        Sale.cancelled.is_(False), Sale.booked_on >= start, Sale.booked_on <= end
    )
    for sale in db.scalars(sales_query):
        cost = sale.quantity * _average_until(made.get(sale.article_id, []), sale.booked_on)
        article = articles[sale.article_id]
        before = lines.get(article.id)
        lines[article.id] = Line(
            article.id,
            article.number,
            article.name,
            (before.quantity if before else 0) + sale.quantity,
            (before.revenue if before else ZERO) + sale.quantity * sale.unit_price,
            (before.cost if before else ZERO) + cost,
        )
    revenue = sum((line.revenue for line in lines.values()), ZERO)
    cost_of_sold = sum((line.cost for line in lines.values()), ZERO)

    # Best first; equal profit by article number, so the order never jumps
    ranked = sorted(lines.values(), key=lambda line: (-line.profit, line.number.casefold()))
    top = ranked[:TOP_SIZE]
    # The worst first, from what is left: an article is never in both lists
    flop = sorted(ranked[TOP_SIZE:], key=lambda line: (line.profit, line.number.casefold()))
    flop = flop[:TOP_SIZE]

    # Failed prints count with their whole cost, the others with their waste amount (issue
    # #216); labour is not part of a print. The production cost of the period is the total of
    # the productions (labour included).
    in_period = (
        Production.cancelled.is_(False),
        Production.booked_on >= start,
        Production.booked_on <= end,
    )
    failed_cost = ZERO
    for print_ in db.scalars(
        select(ProductionPrint)
        .join(Production, Production.id == ProductionPrint.production_id)
        .where(*in_period)
    ):
        if print_.status == PrintStatus.FAILED:
            failed_cost += (
                print_.material_cost
                + print_.electricity_cost
                + print_.wear_cost
                + print_.machine_cost
            )
        else:
            failed_cost += print_.waste_cost
    production_cost = sum(db.scalars(select(Production.total_cost).where(*in_period)), ZERO)
    failed_share = failed_cost * 100 / production_cost if production_cost > 0 else None

    levels = stock.stock_levels(db, list(articles))
    last_sale: dict[int, date] = {}
    for article_id, day in db.execute(
        select(Sale.article_id, Sale.booked_on).where(Sale.cancelled.is_(False))
    ):
        if article_id not in last_sale or day > last_sale[article_id]:
            last_sale[article_id] = day
    limit = today - timedelta(days=SLOW_DAYS)
    stock_value = ZERO
    slow = []
    for article in sorted(articles.values(), key=lambda a: a.number_key):
        pieces = levels[article.id]
        if pieces <= 0:
            continue
        items = made.get(article.id, [])
        stock_value += pieces * _average_until(items, date.max)
        last = last_sale.get(article.id)
        if last is None or last < limit:
            slow.append(SlowRow(article.id, article.number, article.name, pieces, last))

    return Totals(
        revenue=revenue,
        cost_of_sold=cost_of_sold,
        gross_profit=revenue - cost_of_sold,
        stock_value=stock_value,
        failed_cost=failed_cost,
        failed_share=failed_share,
        top=top,
        flop=flop,
        slow=slow,
    )


@dataclass(frozen=True)
class MonthPoint:
    """Revenue and cost of the sold pieces of one month (first day of the month)."""

    month: date
    revenue: Decimal
    cost: Decimal


MONTHS = 12


def _months_back(day: date, count: int) -> date:
    """The first day of the month `count` months before the month of `day`."""
    index = day.year * 12 + day.month - 1 - count
    return date(index // 12, index % 12 + 1, 1)


def monthly(db: Session, today: date) -> list[MonthPoint]:
    """Revenue and cost of the sold pieces per month for the last 12 months up to and including
    the month of `today`, oldest first (issue #207). Cost is valued like in `compute`; months
    without sales are 0."""
    first = _months_back(today, MONTHS - 1)
    made = _productions(db)
    revenue: dict[date, Decimal] = {}
    cost: dict[date, Decimal] = {}
    for sale in db.scalars(
        select(Sale).where(
            Sale.cancelled.is_(False), Sale.booked_on >= first, Sale.booked_on <= _month_end(today)
        )
    ):
        month = _month_start(sale.booked_on)
        revenue[month] = revenue.get(month, ZERO) + sale.quantity * sale.unit_price
        cost[month] = cost.get(month, ZERO) + sale.quantity * _average_until(
            made.get(sale.article_id, []), sale.booked_on
        )
    months = (_months_back(today, MONTHS - 1 - index) for index in range(MONTHS))
    return [MonthPoint(m, revenue.get(m, ZERO), cost.get(m, ZERO)) for m in months]


def texts(totals: Totals, language: str) -> dict[str, str]:
    """The five key figures as shown on the page."""
    return {
        "revenue": money(totals.revenue, language),
        "cost_of_sold": money(totals.cost_of_sold, language),
        "gross_profit": money(totals.gross_profit, language),
        "stock_value": money(totals.stock_value, language),
        "failed_cost": money(totals.failed_cost, language),
        "failed_share": percent(totals.failed_share, language),
    }
