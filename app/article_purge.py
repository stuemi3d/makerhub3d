"""Deleting an article for good (issue #47). Admin only.

The only exception to "never delete, only cancel" (plan, section 4): the article goes away
together with everything that hangs on it. Calculations, productions and sales do not exist
yet; their counts are 0 until M2 to M4 bring them (and add their deletion here).
"""

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app import audit, stock
from app.models import Article, StockMovement

# The kinds of records that can hang on an article, in the order the warning lists them.
COUNT_KEYS = ("calculations", "productions", "stock_movements", "sales")


def counts(db: Session, article_id: int) -> dict[str, int]:
    """How many records of each kind hang on the article."""
    return {
        "calculations": 0,
        "productions": 0,
        "stock_movements": stock.count_movements(db, article_id),
        "sales": 0,
    }


def has_dependents(db: Session, article_id: int) -> bool:
    return any(counts(db, article_id).values())


def purge(db: Session, actor_id: int, article: Article) -> None:
    """Deletes the article and its records and writes the change log, in the caller's
    transaction (all or nothing)."""
    found = counts(db, article.id)
    # A cancellation points to its original; break these links before deleting both.
    db.execute(
        update(StockMovement).where(StockMovement.article_id == article.id).values(cancels_id=None)
    )
    db.execute(delete(StockMovement).where(StockMovement.article_id == article.id))
    subject = f"{article.number} · {article.name}"
    db.delete(article)
    audit.record_change(
        db,
        actor_id,
        "article.purged",
        subject,
        [{"field": key, "old": str(found[key]), "new": "0"} for key in COUNT_KEYS],
    )
