"""Production (issues #118, #140): booking prints and the result article, and cancelling.

A production holds any number of prints (Bambuddy archive entries) and exactly one result
article, which gets all costs. Booking raises its stock; cancelling books a counter movement.
Productions are never changed or deleted afterwards. Costs, including machine (issue #135)
and labour (issue #134), are stored as values.

The caller holds `stock.booking_lock` and the transaction (like for app.stock); a missing
production is for the caller to answer with 404.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, auth, stock
from app.models import (
    Article,
    IgnoredPrint,
    PrintStatus,
    Production,
    ProductionPrint,
    ProductionResult,
    StockMovement,
    StockMovementKind,
)

CENT = Decimal("0.01")
MAX_NOTE = 500
MAX_LABOR = Decimal(1_000_000)


@dataclass(frozen=True)
class PrintInput:
    archive_id: int
    material_cost: Decimal
    electricity_cost: Decimal
    machine_cost: Decimal
    failed: bool = False

    @property
    def cost(self) -> Decimal:
        return self.material_cost + self.electricity_cost + self.machine_cost


@dataclass(frozen=True)
class LaborInput:
    """Working time of the whole production and the hourly rate for it."""

    minutes: Decimal
    rate: Decimal

    @property
    def cost(self) -> Decimal:
        """Minutes / 60 x rate, rounded to the cent."""
        return (self.minutes / 60 * self.rate).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ResultInput:
    article_id: int
    quantity: int


@dataclass(frozen=True)
class PreviewRow:
    article: Article
    quantity: int
    cost: Decimal


def preview(
    db: Session, prints: list[PrintInput], result: ResultInput, labor: LaborInput | None = None
) -> tuple[dict[str, str], Decimal, PreviewRow | None]:
    """What booking would allocate: the errors, the total and the result article with all costs.
    Nothing is stored."""
    errors, article = _check(db, prints, result, "", labor)
    if errors:
        return errors, Decimal(0), None
    total = costs(prints, labor)[2]
    return {}, total, PreviewRow(article, result.quantity, total)


def costs(prints: list[PrintInput], labor: LaborInput | None) -> tuple[Decimal, Decimal, Decimal]:
    """Print cost, labour cost and the total (print + labour), all rounded to the cent."""
    labor_cost = labor.cost if labor else Decimal(0)
    printed = sum((item.cost for item in prints), Decimal(0))
    total = (printed + labor_cost).quantize(CENT, rounding=ROUND_HALF_UP)
    return total - labor_cost, labor_cost, total


def assigned_archive_ids(db: Session) -> set[int]:
    """The prints that are in an active (not cancelled) production."""
    return set(
        db.scalars(
            select(ProductionPrint.archive_id)
            .join(Production, Production.id == ProductionPrint.production_id)
            .where(Production.cancelled.is_(False))
        )
    )


def assigned_productions(db: Session) -> dict[int, int]:
    """For every print in an active production: the id of that production."""
    found = db.execute(
        select(ProductionPrint.archive_id, ProductionPrint.production_id)
        .join(Production, Production.id == ProductionPrint.production_id)
        .where(Production.cancelled.is_(False))
    )
    return {archive_id: production_id for archive_id, production_id in found}


def ignored_archive_ids(db: Session) -> set[int]:
    return set(db.scalars(select(IgnoredPrint.archive_id)))


def unassigned(db: Session, archive_ids: list[int]) -> list[int]:
    """Of the given prints (e.g. the Bambuddy archive), those in no active production and not
    ignored, in the given order."""
    taken = assigned_archive_ids(db) | ignored_archive_ids(db)
    return [archive_id for archive_id in archive_ids if archive_id not in taken]


def ignore(db: Session, actor_id: int | None, archive_id: int) -> str | None:
    """Hides a print from the open list. Returns an error key when it is already in an active
    production; ignoring twice does nothing."""
    if archive_id in assigned_archive_ids(db):
        return "production.error.print_taken"
    if db.get(IgnoredPrint, archive_id) is None:
        db.add(IgnoredPrint(archive_id=archive_id, ignored_on=date.today()))
        audit.record_change(db, actor_id, "production.ignored", str(archive_id))
    return None


def unignore(db: Session, actor_id: int | None, archive_id: int) -> None:
    item = db.get(IgnoredPrint, archive_id)
    if item is not None:
        db.delete(item)
        audit.record_change(db, actor_id, "production.unignored", str(archive_id))


def _check(
    db: Session,
    prints: list[PrintInput],
    result: ResultInput,
    note: str,
    labor: LaborInput | None = None,
) -> tuple[dict[str, str], Article | None]:
    errors: dict[str, str] = {}

    ids = [item.archive_id for item in prints]
    if not prints:
        errors["prints"] = "production.error.no_print"
    elif len(set(ids)) != len(ids):
        errors["prints"] = "production.error.print_twice"
    elif set(ids) & assigned_archive_ids(db):
        errors["prints"] = "production.error.print_taken"
    elif set(ids) & ignored_archive_ids(db):
        errors["prints"] = "production.error.print_ignored"
    elif any(item <= 0 for item in ids):
        errors["prints"] = "production.error.print_id"
    elif any(
        not p.cost.is_finite() or min(p.material_cost, p.electricity_cost, p.machine_cost) < 0
        for p in prints
    ):
        errors["prints"] = "production.error.cost"

    article = db.get(Article, result.article_id)
    if article is None or not article.active:
        errors["article"] = "production.error.article"
    if result.quantity <= 0:
        errors["quantity"] = "production.error.quantity"

    if len(note) > MAX_NOTE:
        errors["note"] = "production.error.note_long"
    if labor is not None:
        for name, value in (("labor_minutes", labor.minutes), ("labor_rate", labor.rate)):
            if not value.is_finite() or not 0 <= value < MAX_LABOR:
                errors[name] = "production.error.labor_number"
    return errors, article


def book(
    db: Session,
    actor_id: int | None,
    booked_on: date,
    note: str,
    prints: list[PrintInput],
    result: ResultInput,
    labor: LaborInput | None = None,
) -> tuple[int | None, dict[str, str]]:
    """Books a production and raises the stock of its result article. Returns the id, or the
    errors by field; nothing is stored when there are errors.

    The total (prints plus labour) is rounded to the cent and all of it goes to the one result
    article. Minutes, rate and labour cost are stored as values."""
    errors, article = _check(db, prints, result, note.strip(), labor)
    if errors:
        return None, errors

    labor = labor or LaborInput(Decimal(0), Decimal(0))
    total = costs(prints, labor)[2]
    now = auth.utcnow()

    item = Production(
        booked_on=booked_on,
        created_at=now,
        note=note.strip(),
        total_cost=total,
        labor_minutes=labor.minutes,
        labor_rate=labor.rate,
        labor_cost=labor.cost,
        user_id=actor_id,
    )
    for print_ in prints:
        item.prints.append(
            ProductionPrint(
                archive_id=print_.archive_id,
                material_cost=print_.material_cost,
                electricity_cost=print_.electricity_cost,
                wear_cost=Decimal(0),
                machine_cost=print_.machine_cost,
                status=PrintStatus.FAILED if print_.failed else PrintStatus.SUCCESS,
            )
        )
    db.add(item)
    movement = StockMovement(
        article_id=article.id,
        booked_on=booked_on,
        created_at=now,
        quantity=result.quantity,
        kind=StockMovementKind.PRODUCTION,
        reason="",
        user_id=actor_id,
    )
    db.add(movement)
    db.flush()
    item.results.append(
        ProductionResult(
            article_id=article.id,
            quantity=result.quantity,
            allocated_cost=total,
            movement_id=movement.id,
        )
    )
    db.flush()
    audit.record_change(
        db,
        actor_id,
        "production.booked",
        str(item.id),
        [
            {"field": "total_cost", "old": "", "new": str(total)},
            {"field": "labor_cost", "old": "", "new": str(labor.cost)},
            {"field": "articles", "old": "", "new": f"{article.number} x {result.quantity}"},
            {"field": "prints", "old": "", "new": ", ".join(str(p.archive_id) for p in prints)},
        ],
    )
    return item.id, {}


def cancel(db: Session, actor_id: int | None, production_id: int) -> str | None:
    """Cancels a production by a counter movement for its result article.
    Returns an error key, or None when done. Raises LookupError for a missing production."""
    item = db.get(Production, production_id)
    if item is None:
        raise LookupError(production_id)
    if item.cancelled:
        return "production.error.cancelled_already"
    for result in item.results:
        if stock.stock_of(db, result.article_id) - result.quantity < 0:
            return "production.error.insufficient"
    now = auth.utcnow()
    for result in item.results:
        db.add(
            StockMovement(
                article_id=result.article_id,
                booked_on=date.today(),
                created_at=now,
                quantity=-result.quantity,
                kind=StockMovementKind.CANCELLATION,
                reason="",
                cancels_id=result.movement_id,
                user_id=actor_id,
            )
        )
    item.cancelled = True
    audit.record_change(
        db,
        actor_id,
        "production.cancelled",
        str(item.id),
        [{"field": "total_cost", "old": str(item.total_cost), "new": ""}],
    )
    return None
