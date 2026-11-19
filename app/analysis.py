"""Analysis per article (issue #203): key figures from the real productions and sales.

Everything is calculated from stored values, nothing is stored again. Cancelled productions and
sales never count. A figure that cannot be calculated (nothing produced or sold yet, no
calculation, no working time) is None; nothing is ever divided by zero. Money is summed in
Python as Decimal (it is stored as text, so the database must not add it up).
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import stock
from app.models import AppSettings, Calculation, Production, ProductionResult, Sale
from app.production_form import money_text

HUNDRED = Decimal(100)
SIXTY = Decimal(60)
ZERO = Decimal(0)
NONE_TEXT = "\u2013"  # en dash
TENTH = Decimal("0.1")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Figures:
    produced: int
    sold: int
    stock: int
    cost_per_piece: Decimal | None
    planned_cost: Decimal | None
    # Percent; negative when the real cost is below the calculation
    deviation: Decimal | None
    warn: bool
    average_price: Decimal | None
    profit: Decimal | None
    profit_percent: Decimal | None
    hourly_wage: Decimal | None
    failed: int
    # Percent of all pieces (good and failed) that failed
    failed_share: Decimal | None


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator else None


def compute(db: Session, article_id: int) -> Figures:
    """The key figures of the article, over its productions and sales that are not cancelled."""
    # A production has exactly one result article (see models.Production), so its labour and
    # failed pieces belong to this article as a whole.
    produced = failed = 0
    cost = labor_cost = labor_minutes = ZERO
    query = (
        select(Production, ProductionResult)
        .join(ProductionResult, ProductionResult.production_id == Production.id)
        .where(ProductionResult.article_id == article_id, Production.cancelled.is_(False))
    )
    for item, result in db.execute(query):
        produced += result.quantity
        failed += item.failed_pieces
        cost += result.allocated_cost
        labor_cost += item.labor_cost
        labor_minutes += item.labor_minutes

    sold = 0
    revenue = ZERO
    for sale in db.scalars(
        select(Sale).where(Sale.article_id == article_id, Sale.cancelled.is_(False))
    ):
        sold += sale.quantity
        revenue += sale.quantity * sale.unit_price

    cost_per_piece = _divide(cost, Decimal(produced))
    cost_without_labor = _divide(cost - labor_cost, Decimal(produced))
    average_price = _divide(revenue, Decimal(sold))

    profit = profit_percent = None
    if average_price is not None and cost_per_piece is not None:
        profit = average_price - cost_per_piece
        profit_percent = _divide(profit * HUNDRED, average_price)

    # Hours of work per piece; no working time means no hourly wage
    hours_per_piece = _divide(labor_minutes, Decimal(produced) * SIXTY)
    hourly_wage = None
    if average_price is not None and cost_without_labor is not None and hours_per_piece:
        hourly_wage = (average_price - cost_without_labor) / hours_per_piece

    planned = db.scalar(
        select(Calculation.cost_with_labour).where(
            Calculation.article_id == article_id, Calculation.is_main.is_(True)
        )
    )
    deviation = None
    if planned is not None and cost_per_piece is not None:
        deviation = _divide((cost_per_piece - planned) * HUNDRED, planned)
    threshold = db.get(AppSettings, 1).deviation_threshold
    warn = deviation is not None and abs(deviation) > threshold

    return Figures(
        produced=produced,
        sold=sold,
        stock=stock.stock_of(db, article_id),
        cost_per_piece=cost_per_piece,
        planned_cost=planned,
        deviation=deviation,
        warn=warn,
        average_price=average_price,
        profit=profit,
        profit_percent=profit_percent,
        hourly_wage=hourly_wage,
        failed=failed,
        failed_share=_divide(Decimal(failed) * HUNDRED, Decimal(produced + failed)),
    )


@dataclass(frozen=True)
class Point:
    """One production on the chart: the day, the good pieces and the cost per piece."""

    day: date
    quantity: int
    cost_per_piece: Decimal


def cost_series(db: Session, article_id: int) -> list[Point]:
    """One point per production that is not cancelled, oldest first (issue #204)."""
    query = (
        select(Production, ProductionResult)
        .join(ProductionResult, ProductionResult.production_id == Production.id)
        .where(ProductionResult.article_id == article_id, Production.cancelled.is_(False))
        .order_by(Production.booked_on, Production.id)
    )
    return [
        Point(item.booked_on, result.quantity, result.allocated_cost / result.quantity)
        for item, result in db.execute(query)
    ]


def _cents(value: Decimal) -> str:
    """Plain number text with a dot and two decimals; the chart reads it, no float on our side."""
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def chart_data(points: list[Point], planned: Decimal | None, labels: dict[str, str], language: str):
    """What the page hands to the chart script (as JSON in a data attribute)."""
    return {
        "points": [
            {"date": p.day.isoformat(), "quantity": p.quantity, "cost": _cents(p.cost_per_piece)}
            for p in points
        ],
        "planned": None if planned is None else _cents(planned),
        "labels": labels,
        "language": language,
    }


TABLE_LIMIT = 20


def point_rows(points: list[Point], language: str) -> list[dict[str, str]]:
    """The newest points as table rows for the page, oldest first (the chart shows all)."""
    return [
        {
            "date": stock.format_date(p.day, language),
            "quantity": str(p.quantity),
            "cost": money(p.cost_per_piece, language),
        }
        for p in points[-TABLE_LIMIT:]
    ]


def money(value: Decimal | None, language: str) -> str:
    if value is None:
        return NONE_TEXT
    return f"{money_text(value.quantize(CENT, rounding=ROUND_HALF_UP), language)} €"


def _percent(value: Decimal | None, language: str) -> str:
    """One decimal at most: 44,4 % but 10 % (not 10,0 %)."""
    if value is None:
        return NONE_TEXT
    text = format(value.quantize(TENTH, rounding=ROUND_HALF_UP), "f")
    if text.endswith(".0"):
        text = text[:-2]
    return (text.replace(".", ",") if language == "de" else text) + " %"


def texts(figures: Figures, language: str) -> dict[str, str]:
    """The figures as shown on the page, by tile; a dash where there is no value."""
    return {
        "cost_actual": money(figures.cost_per_piece, language),
        "cost_planned": money(figures.planned_cost, language),
        "deviation": _percent(figures.deviation, language),
        "price_average": money(figures.average_price, language),
        "profit": money(figures.profit, language),
        "profit_percent": _percent(figures.profit_percent, language),
        "hourly_wage": money(figures.hourly_wage, language),
        "produced": str(figures.produced),
        "sold": str(figures.sold),
        "stock": str(figures.stock),
        "failed": str(figures.failed),
        "failed_share": _percent(figures.failed_share, language),
    }
