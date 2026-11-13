"""Stock movements (issue #11): the stock level, manual bookings and cancelling.

The stock is never stored; it is the sum of all movements of an article. Movements are never
changed or deleted. A mistake is cancelled by a counter booking.
"""

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, auth
from app.models import Article, StockMovement, StockMovementKind, User

BOOK_FIELDS = ("article_id", "type", "quantity", "reason", "date")
MAX_REASON = 200
MAX_DIGITS = 9
TYPES = ("withdrawal", "correction")
_DATE_FORMATS = {"de": "%d.%m.%Y", "en": "%Y-%m-%d"}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_WHOLE = re.compile(rf"\d{{1,{MAX_DIGITS}}}")
_SIGNED = re.compile(rf"[+-]?\d{{1,{MAX_DIGITS}}}")

# Movements made by production and sale are cancelled where they come from (M2 and M5).
CANCELLABLE_KINDS = frozenset({StockMovementKind.CORRECTION})


def stock_of(db: Session, article_id: int) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
            StockMovement.article_id == article_id
        )
    )
    return int(total)


def stock_levels(db: Session, article_ids: list[int]) -> dict[int, int]:
    rows = db.execute(
        select(StockMovement.article_id, func.sum(StockMovement.quantity))
        .where(StockMovement.article_id.in_(article_ids))
        .group_by(StockMovement.article_id)
    )
    levels = {article_id: 0 for article_id in article_ids}
    levels.update({article_id: int(total) for article_id, total in rows})
    return levels


def format_date(value: date, language: str) -> str:
    return value.strftime(_DATE_FORMATS.get(language, _DATE_FORMATS["en"]))


@dataclass(frozen=True)
class MovementRow:
    id: int
    date: str
    article_id: int
    article_number: str
    quantity: str
    kind: str
    reason: str
    cancels_id: int | None
    is_cancelled: bool
    cancellable: bool
    user: str


def movement_rows(db: Session, language: str, article_id: int | None = None) -> list[MovementRow]:
    """The movements, newest first, optionally of one article."""
    cancelled_ids = set(
        db.scalars(select(StockMovement.cancels_id).where(StockMovement.cancels_id.is_not(None)))
    )
    query = (
        select(StockMovement, Article.number, User.username)
        .join(Article, Article.id == StockMovement.article_id)
        .outerjoin(User, User.id == StockMovement.user_id)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
    )
    if article_id is not None:
        query = query.where(StockMovement.article_id == article_id)
    return [
        MovementRow(
            id=movement.id,
            date=format_date(movement.booked_on, language),
            article_id=movement.article_id,
            article_number=number,
            quantity=f"{movement.quantity:+d}",
            kind=movement.kind,
            reason=movement.reason,
            cancels_id=movement.cancels_id,
            is_cancelled=movement.id in cancelled_ids,
            cancellable=movement.kind in CANCELLABLE_KINDS and movement.id not in cancelled_ids,
            user=username or "",
        )
        for movement, number, username in db.execute(query)
    ]


def count_movements(db: Session, article_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(StockMovement)
            .where(StockMovement.article_id == article_id)
        )
    )


def validate_booking(raw: dict[str, str], today: date) -> tuple[dict[str, object], dict[str, str]]:
    """Checks what the form can say by itself. Returns the values and the errors by field.

    `quantity` in the values is already signed: a withdrawal becomes negative.
    """
    errors: dict[str, str] = {}
    values: dict[str, object] = {}

    article_text = raw.get("article_id", "").strip()
    if article_text.isascii() and article_text.isdigit() and len(article_text) <= MAX_DIGITS:
        values["article_id"] = int(article_text)
    else:
        errors["article_id"] = "stock.error.article"

    kind = raw.get("type", "")
    if kind not in TYPES:
        errors["type"] = "stock.error.type"

    quantity_text = raw.get("quantity", "").strip()
    pattern = _WHOLE if kind == "withdrawal" else _SIGNED
    if not pattern.fullmatch(quantity_text):
        errors["quantity"] = "stock.error.quantity"
    elif int(quantity_text) == 0:
        errors["quantity"] = "stock.error.zero"
    elif "type" not in errors:
        number = int(quantity_text)
        values["quantity"] = -number if kind == "withdrawal" else number

    reason = raw.get("reason", "").strip()
    if not reason:
        errors["reason"] = "stock.error.reason"
    elif len(reason) > MAX_REASON:
        errors["reason"] = "stock.error.reason_long"
    values["reason"] = reason

    date_text = raw.get("date", "").strip()
    booked_on = today
    if date_text:
        try:
            if not _ISO_DATE.fullmatch(date_text):
                raise ValueError
            booked_on = date.fromisoformat(date_text)
        except ValueError:
            errors["date"] = "stock.error.date"
    if booked_on > today:
        errors["date"] = "stock.error.date"
    values["date"] = booked_on
    return values, errors


def book(db: Session, actor_id: int, values: dict[str, object]) -> dict[str, str]:
    """Books a manual correction or withdrawal. The caller holds the stock lock and the
    transaction. Returns the errors; nothing is stored when there are some."""
    article = db.get(Article, values["article_id"])
    if article is None:
        return {"article_id": "stock.error.article"}
    if not article.active:
        return {"article_id": "stock.error.inactive"}
    quantity = int(values["quantity"])
    if stock_of(db, article.id) + quantity < 0:
        return {"quantity": "stock.error.insufficient"}
    booked_on = values["date"]
    reason = str(values["reason"])
    db.add(
        StockMovement(
            article_id=article.id,
            booked_on=booked_on,
            created_at=auth.utcnow(),
            quantity=quantity,
            kind=StockMovementKind.CORRECTION,
            reason=reason,
            user_id=actor_id,
        )
    )
    audit.record_change(
        db,
        actor_id,
        "stock.booked",
        article.number,
        [
            {"field": "quantity", "old": "", "new": str(quantity)},
            {"field": "reason", "old": "", "new": reason},
            {"field": "date", "old": "", "new": booked_on.isoformat()},
        ],
    )
    return {}


def cancel(db: Session, actor_id: int, movement_id: int) -> str | None:
    """Cancels a movement by a counter booking. Returns an error key, or None when done.

    The caller holds the stock lock and the transaction; a missing movement is for the
    caller to answer with 404 (checked there).
    """
    movement = db.get(StockMovement, movement_id)
    if movement is None:
        raise LookupError(movement_id)
    if movement.kind == StockMovementKind.CANCELLATION:
        return "stock.error.cancel_cancellation"
    if movement.kind not in CANCELLABLE_KINDS:
        return "stock.error.cancel_at_source"
    if db.scalar(select(StockMovement.id).where(StockMovement.cancels_id == movement.id)):
        return "stock.error.cancelled_already"
    if stock_of(db, movement.article_id) - movement.quantity < 0:
        return "stock.error.insufficient"
    article = db.get(Article, movement.article_id)
    db.add(
        StockMovement(
            article_id=movement.article_id,
            booked_on=date.today(),
            created_at=auth.utcnow(),
            quantity=-movement.quantity,
            kind=StockMovementKind.CANCELLATION,
            reason="",
            cancels_id=movement.id,
            user_id=actor_id,
        )
    )
    audit.record_change(
        db,
        actor_id,
        "stock.cancelled",
        article.number,
        [
            {"field": "quantity", "old": "", "new": str(-movement.quantity)},
            {"field": "cancels", "old": "", "new": str(movement.id)},
        ],
    )
    return None
