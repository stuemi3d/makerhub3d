"""Dashboard, first version (issue #12): key figures and articles below the minimum stock."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import stock
from app.models import Article


@dataclass(frozen=True)
class LowStockRow:
    id: int
    number: str
    name: str
    stock: int
    min_stock: int


@dataclass(frozen=True)
class Dashboard:
    active_articles: int
    total_stock: int
    low_stock: list[LowStockRow]


def build(db: Session) -> Dashboard:
    """Only active articles count. An article is low when its stock is below its minimum;
    equal is fine. The biggest shortfall comes first."""
    articles = list(db.scalars(select(Article).where(Article.active.is_(True))))
    levels = stock.stock_levels(db, [article.id for article in articles])
    low = [
        LowStockRow(a.id, a.number, a.name, levels[a.id], a.min_stock)
        for a in articles
        if a.min_stock is not None and levels[a.id] < a.min_stock
    ]
    low.sort(key=lambda row: (row.stock - row.min_stock, row.number.casefold()))
    return Dashboard(
        active_articles=len(articles),
        total_stock=sum(levels.values()),
        low_stock=low,
    )
