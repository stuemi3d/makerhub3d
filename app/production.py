"""Production (issue #118): booking prints and result articles, cost split and cancelling.

A production holds any number of prints (Bambuddy archive entries) and any number of result
articles. Booking raises the stock of every result article; cancelling books counter movements.
Productions are never changed or deleted afterwards. Costs are stored as values.

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


@dataclass(frozen=True)
class PrintInput:
    archive_id: int
    material_cost: Decimal
    electricity_cost: Decimal
    wear_cost: Decimal
    failed: bool = False

    @property
    def cost(self) -> Decimal:
        return self.material_cost + self.electricity_cost + self.wear_cost


@dataclass(frozen=True)
class ResultInput:
    article_id: int
    quantity: int
    # Empty = the weight of the article (or its template), else 1
    weight: Decimal | None = None


def allocate(total: Decimal, shares: list[Decimal]) -> list[Decimal]:
    """Splits `total` by the shares, to the cent. The rounding remainder goes to the row with
    the largest share, so the parts always add up to the total. One row gets all, unrounded."""
    if len(shares) == 1:
        return [total]
    whole = sum(shares)
    parts = [(total * share / whole).quantize(CENT, rounding=ROUND_HALF_UP) for share in shares]
    parts[shares.index(max(shares))] += total - sum(parts)
    return parts


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


def _weight_of(db: Session, article: Article, given: Decimal | None) -> Decimal:
    if given is not None:
        return given
    if article.weight is not None:
        return article.weight
    if article.template is not None and article.template.weight is not None:
        return article.template.weight
    return Decimal(1)


def _check(
    db: Session, prints: list[PrintInput], results: list[ResultInput], note: str
) -> tuple[dict[str, str], list[tuple[Article, ResultInput, Decimal]]]:
    errors: dict[str, str] = {}
    rows: list[tuple[Article, ResultInput, Decimal]] = []

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
        not p.cost.is_finite() or min(p.material_cost, p.electricity_cost, p.wear_cost) < 0
        for p in prints
    ):
        errors["prints"] = "production.error.cost"

    article_ids = [item.article_id for item in results]
    if not results:
        errors["results"] = "production.error.no_result"
    elif len(set(article_ids)) != len(article_ids):
        errors["results"] = "production.error.article_twice"
    else:
        for item in results:
            article = db.get(Article, item.article_id)
            weight = None if article is None else _weight_of(db, article, item.weight)
            if article is None or not article.active:
                errors["results"] = "production.error.article"
            elif item.quantity <= 0:
                errors["results"] = "production.error.quantity"
            elif not weight.is_finite() or weight <= 0:
                errors["results"] = "production.error.weight"
            else:
                rows.append((article, item, weight))

    if len(note) > MAX_NOTE:
        errors["note"] = "production.error.note_long"
    return errors, rows


def book(
    db: Session,
    actor_id: int | None,
    booked_on: date,
    note: str,
    prints: list[PrintInput],
    results: list[ResultInput],
) -> tuple[int | None, dict[str, str]]:
    """Books a production and raises the stock of its result articles. Returns the id, or the
    errors by field; nothing is stored when there are errors."""
    errors, rows = _check(db, prints, results, note.strip())
    if errors:
        return None, errors

    total = sum((item.cost for item in prints), Decimal(0))
    shares = [Decimal(item.quantity) * weight for _, item, weight in rows]
    costs = allocate(total, shares)
    split = len(rows) > 1
    now = auth.utcnow()

    item = Production(
        booked_on=booked_on,
        created_at=now,
        note=note.strip(),
        total_cost=total,
        user_id=actor_id,
    )
    for print_ in prints:
        item.prints.append(
            ProductionPrint(
                archive_id=print_.archive_id,
                material_cost=print_.material_cost,
                electricity_cost=print_.electricity_cost,
                wear_cost=print_.wear_cost,
                status=PrintStatus.FAILED if print_.failed else PrintStatus.SUCCESS,
            )
        )
    db.add(item)
    for (article, result, weight), cost in zip(rows, costs, strict=True):
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
                weight=weight,
                allocated_cost=cost,
                split=split,
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
            {
                "field": "articles",
                "old": "",
                "new": ", ".join(f"{a.number} x {r.quantity}" for a, r, _ in rows),
            },
            {"field": "prints", "old": "", "new": ", ".join(str(p.archive_id) for p in prints)},
        ],
    )
    return item.id, {}


def cancel(db: Session, actor_id: int | None, production_id: int) -> str | None:
    """Cancels a production by counter movements for all its result articles, or none at all.
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
