"""Sales by hand (issue #201): booking, cancelling and the list.

A sale lowers the stock by a movement of kind sale and stores the price as a value. It is never
deleted, only cancelled (a counter movement). The callers hold the stock lock and the
transaction around `book` and `cancel`.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, auth, stock, table_sort, variants
from app.article_form import DecimalField, _parse_decimal
from app.models import Article, Sale, StockMovement, StockMovementKind, User
from app.production_form import money_text

FIELDS = ("article_id", "quantity", "price", "date", "note")
MAX_NOTE = 200
SOURCE_HAND = "hand"
_PRICE = DecimalField("price", 2, Decimal("100000"), allow_zero=True)
_WHOLE = re.compile(rf"[0-9]{{1,{stock.MAX_DIGITS}}}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def validate(raw: dict[str, str], today: date) -> tuple[dict[str, object], dict[str, str]]:
    """Checks what the form can say by itself. Returns the values and the errors by field."""
    errors: dict[str, str] = {}
    values: dict[str, object] = {}

    article_text = raw.get("article_id", "").strip()
    if article_text.isascii() and article_text.isdigit() and len(article_text) <= stock.MAX_DIGITS:
        values["article_id"] = int(article_text)
    else:
        errors["article_id"] = "sales.error.article"

    quantity_text = raw.get("quantity", "").strip()
    if not _WHOLE.fullmatch(quantity_text):
        errors["quantity"] = "sales.error.quantity"
    elif int(quantity_text) == 0:
        errors["quantity"] = "sales.error.zero"
    else:
        values["quantity"] = int(quantity_text)

    price_text = raw.get("price", "").strip()
    price, price_error = _parse_decimal(price_text, _PRICE)
    if price_error:
        errors["price"] = "sales.error.price"
    else:
        values["price"] = price

    note = raw.get("note", "").strip()
    if len(note) > MAX_NOTE:
        errors["note"] = "sales.error.note_long"
    values["note"] = note

    date_text = raw.get("date", "").strip()
    booked_on = today
    if date_text:
        try:
            if not _ISO_DATE.fullmatch(date_text):
                raise ValueError
            booked_on = date.fromisoformat(date_text)
        except ValueError:
            errors["date"] = "sales.error.date"
    if booked_on > today:
        errors["date"] = "sales.error.date"
    values["date"] = booked_on
    return values, errors


def book(db: Session, actor_id: int, values: dict[str, object]) -> dict[str, str]:
    """Books a sale of the article and lowers its stock. Returns the errors by field; nothing
    is stored when there are some."""
    article = db.get(Article, values["article_id"])
    if article is None:
        return {"article_id": "sales.error.article"}
    if not article.active:
        return {"article_id": "sales.error.inactive"}
    quantity = int(values["quantity"])
    if stock.stock_of(db, article.id) - quantity < 0:
        return {"quantity": "sales.error.insufficient"}
    price: Decimal = values["price"]
    booked_on = values["date"]
    movement = StockMovement(
        article_id=article.id,
        booked_on=booked_on,
        created_at=auth.utcnow(),
        quantity=-quantity,
        kind=StockMovementKind.SALE,
        reason="",
        user_id=actor_id,
    )
    db.add(movement)
    db.flush()
    sale = Sale(
        article_id=article.id,
        booked_on=booked_on,
        created_at=movement.created_at,
        quantity=quantity,
        unit_price=price,
        note=str(values["note"]),
        source=SOURCE_HAND,
        movement_id=movement.id,
        user_id=actor_id,
    )
    db.add(sale)
    db.flush()
    audit.record_change(
        db,
        actor_id,
        "sale.booked",
        article.number,
        [
            {"field": "quantity", "old": "", "new": str(quantity)},
            {"field": "price", "old": "", "new": str(price)},
            {"field": "date", "old": "", "new": booked_on.isoformat()},
        ],
    )
    return {}


def cancel(db: Session, actor_id: int, sale_id: int) -> str | None:
    """Cancels a sale by a counter movement. Returns an error key, or None when done. Raises
    LookupError for a missing sale."""
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise LookupError(sale_id)
    if sale.cancelled:
        return "sales.error.cancelled_already"
    article = db.get(Article, sale.article_id)
    db.add(
        StockMovement(
            article_id=sale.article_id,
            booked_on=date.today(),
            created_at=auth.utcnow(),
            quantity=sale.quantity,
            kind=StockMovementKind.CANCELLATION,
            reason="",
            cancels_id=sale.movement_id,
            user_id=actor_id,
        )
    )
    sale.cancelled = True
    audit.record_change(
        db,
        actor_id,
        "sale.cancelled",
        article.number,
        [
            {"field": "quantity", "old": str(sale.quantity), "new": ""},
            {"field": "price", "old": str(sale.unit_price), "new": ""},
        ],
    )
    return None


def count(db: Session, article_id: int) -> int:
    """The sales of the article that are not cancelled (the number on the article's tile)."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(Sale)
            .where(Sale.article_id == article_id, Sale.cancelled.is_(False))
        )
    )


def count_all(db: Session, article_id: int) -> int:
    """All sales of the article, cancelled ones included (they hang on the article)."""
    return int(
        db.scalar(select(func.count()).select_from(Sale).where(Sale.article_id == article_id))
    )


def suggested_price(article: Article) -> Decimal | None:
    """The target selling price (own or from the template) that the form offers."""
    return variants.effective(article, "selling_price")


@dataclass(frozen=True)
class Row:
    id: int
    date: str
    booked_on: date
    article_id: int
    article_number: str
    article_name: str
    quantity: int
    price: str
    total: str
    total_value: Decimal
    note: str
    user: str
    cancelled: bool


def rows(db: Session, language: str, article_id: int | None = None) -> list[Row]:
    """The sales, newest first, optionally of one article."""
    query = (
        select(Sale, Article.number, Article.name, User.username)
        .join(Article, Article.id == Sale.article_id)
        .outerjoin(User, User.id == Sale.user_id)
        .order_by(Sale.booked_on.desc(), Sale.created_at.desc(), Sale.id.desc())
    )
    if article_id is not None:
        query = query.where(Sale.article_id == article_id)
    return [
        Row(
            id=sale.id,
            date=stock.format_date(sale.booked_on, language),
            booked_on=sale.booked_on,
            article_id=sale.article_id,
            article_number=number,
            article_name=name,
            quantity=sale.quantity,
            price=money_text(sale.unit_price, language),
            total=money_text(sale.unit_price * sale.quantity, language),
            total_value=sale.unit_price * sale.quantity,
            note=sale.note,
            user=username or "",
            cancelled=sale.cancelled,
        )
        for sale, number, name, username in db.execute(query)
    ]


SORT_KEYS = ("date", "article", "quantity", "total")
DEFAULT_SORT = ("date", "desc")


def arranged(
    items: list[Row], base: str, sort: str | None, direction: str | None, keep: dict[str, str]
) -> tuple[list[Row], dict]:
    """The sales in the chosen order (newest first by default) and the links of the headers."""
    chosen = table_sort.chosen_sort(SORT_KEYS, sort, direction)
    key, way = chosen or DEFAULT_SORT
    column = {
        "date": lambda row: row.booked_on,
        "article": lambda row: row.article_number.casefold(),
        "quantity": lambda row: row.quantity,
        "total": lambda row: row.total_value,
    }[key]
    ordered = sorted(items, key=column, reverse=way == "desc")
    return ordered, table_sort.sort_links(base, SORT_KEYS, chosen, DEFAULT_SORT, keep)
