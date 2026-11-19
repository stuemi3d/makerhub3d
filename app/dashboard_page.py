"""What the dashboard shows for a period (issue #207), prepared for the template.

The figures come from the same functions as the analysis of everything (`analysis_total`), so
both pages always agree."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app import analysis_total, dashboard_figures
from app.analysis import NONE_TEXT, money, percent
from app.models import Article

CENT = Decimal("0.01")


@dataclass(frozen=True)
class Overview:
    tiles: dict[str, str]
    failed_share: str
    warnings: list[Article]
    slow_count: int
    chart: dict[str, object]


def number(value: Decimal, language: str, places: int = 2) -> str:
    """A plain number with at most `places` decimals: 2 but not 2,00; 0,5; 1,25."""
    text = format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",") if language == "de" else text


def _cents(value: Decimal) -> str:
    """Plain number text with a dot and two decimals for the chart (no float on our side)."""
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def overview(db: Session, start: date, end: date, today: date, language: str) -> Overview:
    totals = analysis_total.compute(db, start, end, today)
    made = dashboard_figures.production(db, start, end)
    margin = totals.gross_profit * 100 / totals.revenue if totals.revenue else None
    tiles = {
        "revenue": money(totals.revenue, language),
        "profit": money(totals.gross_profit, language),
        "margin": percent(margin, language) if margin is not None else NONE_TEXT,
        "pieces": str(made.pieces),
        "cost": money(made.cost, language),
        "hours": number(made.hours, language),
        "kilograms": number(made.kilograms, language, 3),
        "kwh": number(made.kwh, language),
        "failed_rate": percent(made.failed_rate, language),
        "failed_cost": money(totals.failed_cost, language),
        "stock_value": money(totals.stock_value, language),
    }
    months = analysis_total.monthly(db, today)
    chart = {
        "months": [
            {
                "month": point.month.strftime("%Y-%m"),
                "revenue": _cents(point.revenue),
                "cost": _cents(point.cost),
            }
            for point in months
        ],
        "language": language,
    }
    return Overview(
        tiles=tiles,
        failed_share=percent(totals.failed_share, language),
        warnings=dashboard_figures.warning_articles(db),
        slow_count=len(totals.slow),
        chart=chart,
    )
