"""Productions of one article for the article detail page (issue #120). Read-only."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import stock
from app.models import BambuddyConnection, Production, ProductionResult
from app.production_form import money_text


@dataclass(frozen=True)
class Row:
    production_id: int
    date: str
    quantity: int
    cost: str
    per_piece: str
    failed_pieces: int
    cancelled: bool
    archive_ids: list[int]


@dataclass(frozen=True)
class Totals:
    quantity: int
    cost: str
    per_piece: str


def count(db: Session, article_id: int) -> int:
    """All productions of the article, cancelled ones included (they stay in the list)."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(ProductionResult)
            .where(ProductionResult.article_id == article_id)
        )
    )


def bambuddy_address(db: Session) -> str:
    """The address of Bambuddy for links; empty when not set up."""
    row = db.get(BambuddyConnection, 1)
    return row.url.strip().rstrip("/") if row else ""


def rows(db: Session, language: str, article_id: int) -> tuple[list[Row], Totals | None]:
    """The productions of the article (newest first) and the totals over the ones not
    cancelled; no totals when none is left."""
    query = (
        select(Production, ProductionResult)
        .join(ProductionResult, ProductionResult.production_id == Production.id)
        .where(ProductionResult.article_id == article_id)
        .order_by(Production.booked_on.desc(), Production.created_at.desc(), Production.id.desc())
    )
    shown: list[Row] = []
    quantity = 0
    cost = Decimal(0)
    for item, result in db.execute(query):
        shown.append(
            Row(
                production_id=item.id,
                date=stock.format_date(item.booked_on, language),
                quantity=result.quantity,
                cost=money_text(result.allocated_cost, language),
                per_piece=money_text(result.allocated_cost / result.quantity, language),
                failed_pieces=item.failed_pieces,
                cancelled=item.cancelled,
                archive_ids=[p.archive_id for p in item.prints],
            )
        )
        if not item.cancelled:
            quantity += result.quantity
            cost += result.allocated_cost
    totals = None
    if quantity:
        totals = Totals(
            quantity=quantity,
            cost=money_text(cost, language),
            per_piece=money_text(cost / quantity, language),
        )
    return shown, totals
