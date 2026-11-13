"""Articles (issue #10): list, create, edit, deactivate and the detail page with tiles.

Articles are only deactivated; the one way to delete one is the admin's "delete for good"
(issue #47, app/article_purge.py). Calculations, productions and
sales do not exist yet; their tiles show 0 and open empty pages that later milestones fill.
The stock tile counts the stock movements (issue #11).
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import article_purge, audit, auth, stock
from app.article_form import FIELD_NAMES, form_values, number_key, validate
from app.models import Article
from app.permissions import Permission, requires
from app.settings_form import format_decimal

# Order of the tiles on the detail page. The counts are 0 until M2 to M4 bring the data.
SECTIONS = ("calculations", "productions", "stock", "sales", "analysis")


def _stored_values(article: Article) -> dict[str, object]:
    return {name: getattr(article, name) for name in FIELD_NAMES}


def _logged(values: dict[str, object]) -> dict[str, object]:
    """Values for the change log: stored text, empty instead of None."""
    return {name: "" if value is None else value for name, value in values.items()}


def add_article_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
    language: str,
) -> None:
    view = [requires(Permission.VIEW)]
    edit = [requires(Permission.EDIT)]
    manage = [requires(Permission.MANAGE)]

    def shown(value: Decimal | None) -> str | None:
        return None if value is None else format_decimal(value.quantize(Decimal("0.01")), language)

    def article_view(article: Article) -> dict:
        return {
            "id": article.id,
            "number": article.number,
            "name": article.name,
            "selling_price": shown(article.selling_price),
            "weight": shown(article.weight),
            "min_stock": article.min_stock,
            "active": article.active,
        }

    def load(article_id: int) -> dict:
        with new_session() as db:
            article = db.get(Article, article_id)
            if article is None:
                raise HTTPException(status_code=404)
            return article_view(article)

    @app.get("/articles", response_class=HTMLResponse, dependencies=view)
    def article_list(
        request: Request, inactive: str | None = None, purged: str | None = None
    ) -> HTMLResponse:
        show_inactive = inactive == "1"
        with new_session() as db:
            query = select(Article).order_by(Article.number_key)
            if not show_inactive:
                query = query.where(Article.active.is_(True))
            items = [article_view(article) for article in db.scalars(query)]
        return render(
            request,
            "articles.html",
            "nav.articles",
            articles=items,
            show_inactive=show_inactive,
            purged=purged == "1",
        )

    @app.get("/articles/new", response_class=HTMLResponse, dependencies=edit)
    def new_article_page(request: Request) -> HTMLResponse:
        return render(
            request, "article_form.html", "nav.articles", article=None, values={}, errors={}
        )

    def create(actor_id: int, raw: dict[str, str]) -> tuple[dict[str, str], int | None]:
        """Saves a new article; returns the errors (empty when saved) and the new id."""
        values, errors = validate(raw)
        if errors:
            return errors, None
        try:
            with new_session() as db, db.begin():
                if db.scalar(
                    select(Article.id).where(Article.number_key == number_key(values["number"]))
                ):
                    return {"number": "article.error.taken"}, None
                article = Article(
                    **values,
                    number_key=number_key(values["number"]),
                    active=True,
                    created_at=auth.utcnow(),
                )
                db.add(article)
                db.flush()
                audit.record_change(db, actor_id, "article.created", article.number)
                return {}, article.id
        except IntegrityError:
            # Two saves with the same number at the same moment: the database decides.
            return {"number": "article.error.taken"}, None

    @app.post("/articles/new", response_class=HTMLResponse, dependencies=edit)
    async def create_article(request: Request) -> Response:
        raw = await read_form(request, FIELD_NAMES)
        errors, article_id = await run_in_threadpool(create, request.state.user.id, raw)
        if errors:
            return render(
                request,
                "article_form.html",
                "nav.articles",
                status_code=422,
                article=None,
                values=raw,
                errors=errors,
            )
        # The address is built from the stored article, not from the request.
        return RedirectResponse(f"/articles/{int(article_id)}", status_code=303)

    @app.get("/articles/{article_id}", response_class=HTMLResponse, dependencies=view)
    def article_detail(request: Request, article_id: int, saved: str | None = None) -> HTMLResponse:
        article = load(article_id)
        counts = {section: 0 for section in SECTIONS}
        with new_session() as db:
            counts["stock"] = stock.count_movements(db, article_id)
            can_purge = article_purge.has_dependents(db, article_id)
        return render(
            request,
            "article_detail.html",
            "nav.articles",
            article=article,
            sections=SECTIONS,
            counts=counts,
            saved=saved == "1",
            can_purge=can_purge,
        )

    @app.get("/articles/{article_id}/edit", response_class=HTMLResponse, dependencies=edit)
    def edit_article_page(request: Request, article_id: int) -> HTMLResponse:
        with new_session() as db:
            article = db.get(Article, article_id)
            if article is None:
                raise HTTPException(status_code=404)
            values = form_values(_stored_values(article), language)
            current = article_view(article)
        return render(
            request, "article_form.html", "nav.articles", article=current, values=values, errors={}
        )

    def change(article_id: int, actor_id: int, raw: dict[str, str]) -> tuple[dict[str, str], dict]:
        values, errors = validate(raw)
        try:
            with new_session() as db, db.begin():
                article = db.get(Article, article_id)
                if article is None:
                    raise HTTPException(status_code=404)
                current = article_view(article)
                if errors:
                    return errors, current
                taken = db.scalar(
                    select(Article.id).where(
                        Article.number_key == number_key(values["number"]),
                        Article.id != article.id,
                    )
                )
                if taken:
                    return {"number": "article.error.taken"}, current
                details = audit.field_changes(_logged(_stored_values(article)), _logged(values))
                if details:
                    for name, value in values.items():
                        setattr(article, name, value)
                    article.number_key = number_key(article.number)
                    db.flush()
                    audit.record_change(db, actor_id, "article.changed", article.number, details)
                return {}, article_view(article)
        except IntegrityError:
            return {"number": "article.error.taken"}, load(article_id)

    @app.post("/articles/{article_id}", response_class=HTMLResponse, dependencies=edit)
    async def save_article(request: Request, article_id: int) -> Response:
        raw = await read_form(request, FIELD_NAMES)
        errors, article = await run_in_threadpool(change, article_id, request.state.user.id, raw)
        if errors:
            return render(
                request,
                "article_form.html",
                "nav.articles",
                status_code=422,
                article=article,
                values=raw,
                errors=errors,
            )
        return RedirectResponse(f"/articles/{int(article['id'])}?saved=1", status_code=303)

    def set_active(article_id: int, actor_id: int, active: bool) -> int:
        with new_session() as db, db.begin():
            article = db.get(Article, article_id)
            if article is None:
                raise HTTPException(status_code=404)
            if article.active != active:
                article.active = active
                action = "article.activated" if active else "article.deactivated"
                audit.record_change(db, actor_id, action, article.number)
            return article.id

    @app.post("/articles/{article_id}/deactivate", dependencies=edit)
    async def deactivate_article(request: Request, article_id: int) -> Response:
        stored_id = await run_in_threadpool(set_active, article_id, request.state.user.id, False)
        return RedirectResponse(f"/articles/{int(stored_id)}", status_code=303)

    @app.post("/articles/{article_id}/activate", dependencies=edit)
    async def activate_article(request: Request, article_id: int) -> Response:
        stored_id = await run_in_threadpool(set_active, article_id, request.state.user.id, True)
        return RedirectResponse(f"/articles/{int(stored_id)}", status_code=303)

    def purge_view(article_id: int) -> tuple[dict, dict[str, int]]:
        """The article and its counts; 404 when it is gone or nothing hangs on it."""
        with new_session() as db:
            article = db.get(Article, article_id)
            if article is None or not article_purge.has_dependents(db, article_id):
                raise HTTPException(status_code=404)
            return article_view(article), article_purge.counts(db, article_id)

    def purge_page(request: Request, article_id: int, status_code: int = 200, error: bool = False):
        article, found = purge_view(article_id)
        return render(
            request,
            "article_purge.html",
            "nav.articles",
            status_code=status_code,
            article=article,
            counts=found,
            count_keys=article_purge.COUNT_KEYS,
            error=error,
        )

    @app.get("/articles/{article_id}/purge", response_class=HTMLResponse, dependencies=manage)
    def purge_article_page(request: Request, article_id: int) -> HTMLResponse:
        return purge_page(request, article_id)

    def do_purge(article_id: int, actor_id: int, typed: str) -> bool:
        """True when deleted, False when the typed number does not match."""
        with new_session() as db, db.begin():
            article = db.get(Article, article_id)
            if article is None or not article_purge.has_dependents(db, article_id):
                raise HTTPException(status_code=404)
            if typed != article.number:
                return False
            article_purge.purge(db, actor_id, article)
            return True

    @app.post("/articles/{article_id}/purge", dependencies=manage)
    async def purge_article(request: Request, article_id: int) -> Response:
        raw = await read_form(request, ("number",))
        done = await run_in_threadpool(
            do_purge, article_id, request.state.user.id, raw.get("number", "")
        )
        if done:
            return RedirectResponse("/articles?purged=1", status_code=303)
        return await run_in_threadpool(purge_page, request, article_id, 422, True)

    @app.get("/articles/{article_id}/{section}", response_class=HTMLResponse, dependencies=view)
    def article_section(request: Request, article_id: int, section: str) -> HTMLResponse:
        if section not in SECTIONS:
            raise HTTPException(status_code=404)
        if section == "stock":
            article = load(article_id)
            with new_session() as db:
                rows = stock.movement_rows(db, language, article_id)
                current_stock = stock.stock_of(db, article_id)
            return render(
                request,
                "article_stock.html",
                "nav.articles",
                article=article,
                rows=rows,
                current_stock=current_stock,
            )
        return render(
            request,
            "article_section.html",
            "nav.articles",
            article=load(article_id),
            section=section,
        )
