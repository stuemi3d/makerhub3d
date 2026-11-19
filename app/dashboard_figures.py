"""Figures of the dashboard (issue #207) that the analysis of everything does not have: the
production of the period, the quality and the articles to look at.

Sales, stock value and failed cost come from `analysis_total` (one calculation, not two).
Cancelled productions never count; an unknown value of a print counts 0. Money, grams and
hours are Decimal.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import analysis
from app.models import Article, Production, ProductionPrint, ProductionResult

ZERO = Decimal(0)
SECONDS_PER_HOUR = Decimal(3600)
GRAMS_PER_KILOGRAM = Decimal(1000)


@dataclass(frozen=True)
class ProductionFigures:
    pieces: int
    cost: Decimal
    hours: Decimal
    kilograms: Decimal
    kwh: Decimal
    failed_pieces: int
    # Percent of all pieces (good and failed) that failed; None without any piece
    failed_rate: Decimal | None


def production(db: Session, start: date, end: date) -> ProductionFigures:
    """Figures of the productions booked in the period (both days included)."""
    in_period = (
        Production.cancelled.is_(False),
        Production.booked_on >= start,
        Production.booked_on <= end,
    )
    pieces = sum(
        db.scalars(
            select(ProductionResult.quantity)
            .join(Production, Production.id == ProductionResult.production_id)
            .where(*in_period)
        )
    )
    cost = sum(db.scalars(select(Production.total_cost).where(*in_period)), ZERO)
    failed = sum(db.scalars(select(Production.failed_pieces).where(*in_period)))

    seconds = 0
    grams = kwh = ZERO
    for print_seconds, print_grams, print_kwh in db.execute(
        select(
            ProductionPrint.print_seconds,
            ProductionPrint.filament_grams,
            ProductionPrint.energy_kwh,
        )
        .join(Production, Production.id == ProductionPrint.production_id)
        .where(*in_period)
    ):
        seconds += print_seconds or 0
        grams += print_grams or ZERO
        kwh += print_kwh or ZERO

    total = pieces + failed
    return ProductionFigures(
        pieces=pieces,
        cost=cost,
        hours=Decimal(seconds) / SECONDS_PER_HOUR,
        kilograms=grams / GRAMS_PER_KILOGRAM,
        kwh=kwh,
        failed_pieces=failed,
        failed_rate=Decimal(failed) * 100 / total if total else None,
    )


def warning_articles(db: Session) -> list[Article]:
    """Articles whose real cost per piece is over the threshold away from the calculation
    (the warning of the analysis per article), by article number."""
    produced = set(
        db.scalars(
            select(ProductionResult.article_id)
            .join(Production, Production.id == ProductionResult.production_id)
            .where(Production.cancelled.is_(False))
        )
    )
    articles = db.scalars(select(Article).order_by(Article.number_key))
    return [a for a in articles if a.id in produced and analysis.compute(db, a.id).warn]
