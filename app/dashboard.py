"""Dashboard, first version (issue #12): key figures and articles below the minimum stock."""

from dataclasses import dataclass
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import stock, variants
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
    # A variant without an own minimum uses the template's (issue #49).
    minimums = {a.id: variants.effective(a, "min_stock") for a in articles}
    low = [
        LowStockRow(a.id, a.number, a.name, levels[a.id], minimums[a.id])
        for a in articles
        if minimums[a.id] is not None and levels[a.id] < minimums[a.id]
    ]
    low.sort(key=lambda row: (row.stock - row.min_stock, row.number.casefold()))
    return Dashboard(
        active_articles=len(articles),
        total_stock=sum(levels.values()),
        low_stock=low,
    )


# Sortable columns of the list below the minimum stock (issue #77). Without a choice the
# biggest shortfall comes first, so that column counts as chosen, descending.
SORT_KEYS = ("number", "name", "stock", "min_stock", "shortfall")
DEFAULT_SORT = ("shortfall", "desc")


def chosen_sort(sort: str | None, direction: str | None) -> tuple[str, str] | None:
    """The sort from the address, or None (default order) if it is missing or invalid."""
    if sort not in SORT_KEYS or direction not in (None, "asc", "desc"):
        return None
    return sort, direction or "asc"


def sort_rows(rows: list[LowStockRow], key: str, direction: str) -> list[LowStockRow]:
    """Sorts the rows; ties keep the number order."""
    by_number = sorted(rows, key=lambda row: row.number.casefold())
    column = {
        "number": lambda row: row.number.casefold(),
        "name": lambda row: row.name.casefold(),
        "stock": lambda row: row.stock,
        "min_stock": lambda row: row.min_stock,
        "shortfall": lambda row: row.min_stock - row.stock,
    }[key]
    return sorted(by_number, key=column, reverse=direction == "desc")


def sort_links(chosen: tuple[str, str] | None) -> dict[str, dict[str, str]]:
    """Per column: where a click leads and the aria-sort state. A click on the sorted column
    reverses it."""
    current = chosen or DEFAULT_SORT
    links = {}
    for key in SORT_KEYS:
        active = key == current[0]
        next_dir = "desc" if active and current[1] == "asc" else "asc"
        links[key] = {
            "href": "/?" + urlencode({"sort": key, "dir": next_dir}),
            "aria": ("ascending" if current[1] == "asc" else "descending") if active else "none",
        }
    return links
