"""Sales pages (issue #201): list, new sale and cancelling, as pop-ups like the stock pages."""

from collections.abc import Awaitable, Callable
from datetime import date

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sales, stock
from app.article_pages import confirmed, saved_in_popup, varied, wants_popup
from app.models import Article, Sale
from app.permissions import Permission, requires
from app.production_form import money_text

# Ids beyond this cannot exist (and would overflow the database): answered with 404
MAX_ID = 2**31 - 1


def add_sales_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
    language: str,
) -> None:
    view = [requires(Permission.VIEW)]
    edit = [requires(Permission.EDIT)]

    def article_id_of(text: str | None) -> int | None:
        return (
            int(text) if text and text.isascii() and text.isdecimal() and len(text) < 10 else None
        )

    @app.get("/sales", response_class=HTMLResponse, dependencies=view)
    def sales_page(
        request: Request,
        article: str | None = None,
        sort: str | None = None,
        direction: str | None = Query(None, alias="dir"),
    ) -> HTMLResponse:
        article_id = article_id_of(article)
        with new_session() as db:
            items = sales.rows(db, language, article_id)
            articles = [
                {"id": a.id, "number": a.number, "name": a.name}
                for a in db.scalars(select(Article).order_by(Article.number_key))
            ]
        keep = {"article": str(article_id)} if article_id is not None else {}
        items, links = sales.arranged(items, "/sales", sort, direction, keep)
        return render(
            request,
            "sales.html",
            "nav.sales",
            rows=items,
            sort_links=links,
            articles=articles,
            selected=article_id,
        )

    def form_page(
        request: Request, status_code: int = 200, values: dict | None = None, errors=None
    ) -> HTMLResponse:
        with new_session() as db:
            query = select(Article).where(Article.active.is_(True)).order_by(Article.number_key)
            articles = [
                {
                    "id": a.id,
                    "number": a.number,
                    "name": a.name,
                    "price": money_text(price, language)
                    if (price := sales.suggested_price(a)) is not None
                    else "",
                }
                for a in db.scalars(query)
            ]
        values = dict(values or {})
        if "price" not in values:
            chosen = next((a for a in articles if str(a["id"]) == values.get("article_id")), None)
            values["price"] = chosen["price"] if chosen else ""
        return varied(
            render(
                request,
                "sale_form.html",
                "nav.sales",
                status_code=status_code,
                popup=wants_popup(request),
                articles=articles,
                values=values,
                errors=errors or {},
                today=date.today().isoformat(),
            )
        )

    @app.get("/sales/new", response_class=HTMLResponse, dependencies=edit)
    def new_sale_page(request: Request, article: str | None = None) -> HTMLResponse:
        article_id = article_id_of(article)
        return form_page(request, values={"article_id": str(article_id)} if article_id else {})

    def make_sale(actor_id: int, raw: dict[str, str]) -> dict[str, str]:
        values, errors = sales.validate(raw, date.today())
        if errors:
            return errors
        with stock.booking_lock, new_session() as db, db.begin():
            return sales.book(db, actor_id, values)

    @app.post("/sales/new", response_class=HTMLResponse, dependencies=edit)
    async def create_sale(request: Request) -> Response:
        raw = await read_form(request, sales.FIELDS)
        errors = await run_in_threadpool(make_sale, request.state.user.id, raw)
        if errors:
            return form_page(request, status_code=422, values=raw, errors=errors)
        if wants_popup(request):
            return saved_in_popup()
        return RedirectResponse("/sales", status_code=303)

    def cancel_page(
        request: Request, sale_id: int, status_code: int = 200, error: str | None = None
    ) -> HTMLResponse:
        if not 0 < sale_id <= MAX_ID:
            raise HTTPException(status_code=404)
        with new_session() as db:
            sale = db.get(Sale, sale_id)
            if sale is None:
                raise HTTPException(status_code=404)
            article = db.get(Article, sale.article_id)
            item = {
                "id": sale.id,
                "date": stock.format_date(sale.booked_on, language),
                "number": article.number,
                "name": article.name,
                "quantity": sale.quantity,
                "total": money_text(sale.unit_price * sale.quantity, language),
                "article_id": article.id,
            }
        return varied(
            render(
                request,
                "sale_cancel.html",
                "nav.sales",
                status_code=status_code,
                popup=wants_popup(request),
                item=item,
                error=error,
            )
        )

    @app.get("/sales/{sale_id}/cancel", response_class=HTMLResponse, dependencies=edit)
    def cancel_question(request: Request, sale_id: int) -> HTMLResponse:
        return cancel_page(request, sale_id)

    def do_cancel(request: Request, sale_id: int) -> Response:
        if not 0 < sale_id <= MAX_ID:
            raise HTTPException(status_code=404)
        try:
            with stock.booking_lock, new_session() as db, db.begin():
                error = sales.cancel(db, request.state.user.id, sale_id)
        except LookupError:
            raise HTTPException(status_code=404) from None
        if error:
            return cancel_page(request, sale_id, 422, error)
        return confirmed(request, "/sales")

    @app.post("/sales/{sale_id}/cancel", response_class=HTMLResponse, dependencies=edit)
    async def cancel_sale(request: Request, sale_id: int) -> Response:
        return await run_in_threadpool(do_cancel, request, sale_id)
