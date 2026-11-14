"""Stock pages (issue #11): stock per article, manual booking, list of movements, cancelling."""

from collections.abc import Awaitable, Callable
from datetime import date
from threading import Lock
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import stock, table_sort, variants
from app.article_pages import saved_in_popup, wants_popup
from app.models import Article
from app.permissions import Permission, requires

# Checking the stock and booking must not overlap, or two bookings could together push the
# stock below zero. One lock is enough: the app runs as one process with one database.
_booking_lock = Lock()


LEVEL_SORT_KEYS = ("number", "name", "stock", "min_stock")


def sort_levels(items: list[dict], key: str, direction: str) -> list[dict]:
    """Sorts the stock rows; ties keep the number order. Articles without a minimum stay at
    the end, whichever the direction."""
    reverse = direction == "desc"
    if key == "min_stock":
        with_min = [item for item in items if item["min_stock"] is not None]
        without = [item for item in items if item["min_stock"] is None]
        return sorted(with_min, key=lambda item: item["min_stock"], reverse=reverse) + without
    column = {
        "number": lambda item: item["number"].casefold(),
        "name": lambda item: item["name"].casefold(),
        "stock": lambda item: item["stock"],
    }[key]
    return sorted(items, key=column, reverse=reverse)


def add_stock_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
    language: str,
) -> None:
    view = [requires(Permission.VIEW)]
    edit = [requires(Permission.EDIT)]

    def levels(show_inactive: bool, chosen: tuple[str, str] | None) -> list[dict]:
        with new_session() as db:
            query = select(Article).order_by(Article.number_key)
            if not show_inactive:
                query = query.where(Article.active.is_(True))
            articles = list(db.scalars(query))
            stock_by_id = stock.stock_levels(db, [article.id for article in articles])
            minimums = {a.id: variants.effective(a, "min_stock") for a in articles}
            items = [
                {
                    "id": article.id,
                    "number": article.number,
                    "name": article.name,
                    "stock": stock_by_id[article.id],
                    "min_stock": minimums[article.id],
                    "low": minimums[article.id] is not None
                    and stock_by_id[article.id] < minimums[article.id],
                    "active": article.active,
                }
                for article in articles
            ]
        return sort_levels(items, *(chosen or ("number", "asc")))

    @app.get("/stock", response_class=HTMLResponse, dependencies=view)
    def stock_page(
        request: Request,
        inactive: str | None = None,
        sort: str | None = None,
        direction: str | None = Query(None, alias="dir"),
    ) -> HTMLResponse:
        show_inactive = inactive == "1"
        chosen = table_sort.chosen_sort(LEVEL_SORT_KEYS, sort, direction)
        keep = {"inactive": "1"} if show_inactive else {}
        other = {} if show_inactive else {"inactive": "1"}
        if chosen:
            other.update(sort=chosen[0], dir=chosen[1])
        return render(
            request,
            "stock.html",
            "nav.stock",
            items=levels(show_inactive, chosen),
            show_inactive=show_inactive,
            sort_links=table_sort.sort_links(
                "/stock", LEVEL_SORT_KEYS, chosen, ("number", "asc"), keep
            ),
            toggle_inactive_href="/stock" + ("?" + urlencode(other) if other else ""),
        )

    def active_articles() -> list[dict]:
        with new_session() as db:
            query = select(Article).where(Article.active.is_(True)).order_by(Article.number_key)
            return [{"id": a.id, "number": a.number, "name": a.name} for a in db.scalars(query)]

    def booking_page(
        request: Request, status_code: int = 200, values: dict | None = None, errors=None
    ) -> HTMLResponse:
        response = render(
            request,
            "stock_form.html",
            "nav.stock",
            status_code=status_code,
            popup=wants_popup(request),
            articles=active_articles(),
            values=values or {},
            errors=errors or {},
            today=date.today().isoformat(),
        )
        # The same address gives a different answer for HTMX, so caches must tell them apart.
        response.headers["Vary"] = "HX-Request"
        return response

    @app.get("/stock/new", response_class=HTMLResponse, dependencies=edit)
    def new_booking_page(request: Request, article: str | None = None) -> HTMLResponse:
        values = {"article_id": article} if article and article.isdecimal() else {}
        return booking_page(request, values=values)

    def make_booking(actor_id: int, raw: dict[str, str]) -> dict[str, str]:
        values, errors = stock.validate_booking(raw, date.today())
        if errors:
            return errors
        with _booking_lock, new_session() as db, db.begin():
            return stock.book(db, actor_id, values)

    @app.post("/stock/new", response_class=HTMLResponse, dependencies=edit)
    async def book_stock(request: Request) -> Response:
        raw = await read_form(request, stock.BOOK_FIELDS)
        errors = await run_in_threadpool(make_booking, request.state.user.id, raw)
        if errors:
            return booking_page(request, status_code=422, values=raw, errors=errors)
        if wants_popup(request):
            return saved_in_popup()
        return RedirectResponse("/stock", status_code=303)

    def movement_list(
        request: Request,
        article_id: int | None,
        status_code: int = 200,
        error: str | None = None,
        sort: str | None = None,
        direction: str | None = None,
    ) -> HTMLResponse:
        with new_session() as db:
            rows = stock.movement_rows(db, language, article_id)
            articles = [
                {"id": a.id, "number": a.number, "name": a.name}
                for a in db.scalars(select(Article).order_by(Article.number_key))
            ]
        keep = {"article": str(article_id)} if article_id is not None else {}
        rows, links = stock.arranged_movements(rows, "/stock/movements", sort, direction, keep)
        return render(
            request,
            "stock_movements.html",
            "nav.stock",
            status_code=status_code,
            rows=rows,
            sort_links=links,
            articles=articles,
            selected=article_id,
            error=error,
        )

    @app.get("/stock/movements", response_class=HTMLResponse, dependencies=view)
    def movements_page(
        request: Request,
        article: str | None = None,
        sort: str | None = None,
        direction: str | None = Query(None, alias="dir"),
    ) -> HTMLResponse:
        article_id = int(article) if article and article.isdecimal() and len(article) < 10 else None
        return movement_list(request, article_id, sort=sort, direction=direction)

    def cancel_movement(movement_id: int, actor_id: int) -> str | None:
        try:
            with _booking_lock, new_session() as db, db.begin():
                return stock.cancel(db, actor_id, movement_id)
        except LookupError:
            raise HTTPException(status_code=404) from None
        except IntegrityError:
            # Two cancels of the same booking at the same moment: the database decides.
            return "stock.error.cancelled_already"

    @app.post(
        "/stock/movements/{movement_id}/cancel", response_class=HTMLResponse, dependencies=edit
    )
    async def cancel_stock_movement(request: Request, movement_id: int) -> Response:
        error = await run_in_threadpool(cancel_movement, movement_id, request.state.user.id)
        if error:
            return movement_list(request, None, status_code=422, error=error)
        return RedirectResponse("/stock/movements", status_code=303)
