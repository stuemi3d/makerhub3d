"""Articles (issue #10): list, create, edit, deactivate and the detail page with tiles.

Articles are only deactivated; the one way to delete one is the admin's "delete for good"
(issue #47, app/article_purge.py). Calculations, productions and
sales do not exist yet; their tiles show 0 and open empty pages that later milestones fill.
The stock tile counts the stock movements (issue #11).
"""

import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from app import article_purge, audit, auth, stock, variants
from app.article_form import FIELD_NAMES, form_values, number_key, validate
from app.models import Article
from app.permissions import Permission, requires
from app.settings_form import format_decimal

# Order of the tiles on the detail page. The counts are 0 until M2 to M4 bring the data.
SECTIONS = ("calculations", "productions", "stock", "sales", "analysis")


SORT_KEYS = ("number", "name", "price", "stock", "status")


def chosen_sort(sort: str | None, direction: str | None) -> tuple[str, str] | None:
    """The sort from the address, or None (default order) if it is missing or invalid."""
    if sort is None:
        return None
    if sort not in SORT_KEYS or direction not in (None, "asc", "desc"):
        return None
    return sort, direction or "asc"


def list_url(chosen: tuple[str, str] | None, inactive: bool) -> str:
    """The list address with the chosen sorting kept, with or without inactive articles."""
    params = {"inactive": "1"} if inactive else {}
    if chosen:
        params["sort"], params["dir"] = chosen
    return "/articles" + ("?" + urlencode(params) if params else "")


def sort_links(chosen: tuple[str, str] | None, inactive: bool) -> dict[str, dict[str, str]]:
    """Per column: where a click leads and the aria-sort state. A click on the sorted column
    reverses it; the default order (no choice) counts as number ascending."""
    current = chosen or ("number", "asc")
    links = {}
    for key in SORT_KEYS:
        active = key == current[0]
        next_dir = "desc" if active and current[1] == "asc" else "asc"
        params = {"inactive": "1"} if inactive else {}
        params.update(sort=key, dir=next_dir)
        links[key] = {
            "href": "/articles?" + urlencode(params),
            "aria": ("ascending" if current[1] == "asc" else "descending") if active else "none",
        }
    return links


def sort_articles(items: list[dict], key: str, direction: str) -> list[dict]:
    """Sorts the rows; ties keep the number order. Articles without a price stay at the end,
    whichever the direction."""
    reverse = direction == "desc"
    if key == "number":
        return sorted(items, key=lambda item: item["number"].casefold(), reverse=reverse)
    if key == "price":
        priced = [item for item in items if item["price_value"] is not None]
        unpriced = [item for item in items if item["price_value"] is None]
        return sorted(priced, key=lambda item: item["price_value"], reverse=reverse) + unpriced
    column = {
        "name": lambda item: item["name"].casefold(),
        "stock": lambda item: item["stock"],
        "status": lambda item: item["active"],
    }[key]
    return sorted(items, key=column, reverse=reverse)


def _stored_values(article: Article) -> dict[str, object]:
    return {name: getattr(article, name) for name in FIELD_NAMES}


def _logged(values: dict[str, object]) -> dict[str, object]:
    """Values for the change log: stored text, empty instead of None."""
    return {name: "" if value is None else value for name, value in values.items()}


def _attribute_fields(db: Session, article: Article) -> list[dict]:
    """The attributes a variant has values for (the template's); empty for a normal article."""
    if article.template_id is None:
        return []
    return variants.attribute_fields(db, article.template_id)


def wants_popup(request: Request) -> bool:
    """True when HTMX asks for the form to show it in the pop-up (without the page around it)."""
    return request.headers.get("HX-Request") == "true"


def saved_in_popup() -> Response:
    """The answer to a pop-up form that was saved: no content; the page closes the pop-up and
    refreshes its list (see app.js)."""
    return Response(status_code=204, headers={"HX-Trigger": json.dumps({"popup-saved": {}})})


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
        """The article as the pages show it. A variant shows the values that count (its own,
        else the template's) and, per field, where each comes from."""
        template = article.template
        return {
            "id": article.id,
            "number": article.number,
            "name": article.name,
            "selling_price": shown(variants.effective(article, "selling_price")),
            "weight": shown(variants.effective(article, "weight")),
            "min_stock": variants.effective(article, "min_stock"),
            "active": article.active,
            "template": None if template is None else {"id": template.id, "name": template.name},
            "inherited": variants.inherited_texts(template, language) if template else {},
            "sources": {name: variants.source(article, name) for name in variants.INHERITED_FIELDS}
            if template
            else {},
            "attributes": variants.attribute_rows(object_session(article), article),
        }

    def load(article_id: int) -> dict:
        with new_session() as db:
            article = db.get(Article, article_id)
            if article is None:
                raise HTTPException(status_code=404)
            return article_view(article)

    @app.get("/articles", response_class=HTMLResponse, dependencies=view)
    def article_list(
        request: Request,
        inactive: str | None = None,
        purged: str | None = None,
        sort: str | None = None,
        direction: str | None = Query(None, alias="dir"),
    ) -> HTMLResponse:
        show_inactive = inactive == "1"
        chosen = chosen_sort(sort, direction)
        with new_session() as db:
            query = select(Article).order_by(Article.number_key)
            if not show_inactive:
                query = query.where(Article.active.is_(True))
            articles = list(db.scalars(query))
            levels = stock.stock_levels(db, [article.id for article in articles])
            items = []
            for article in articles:
                item = article_view(article)
                item["stock"] = levels[article.id]
                item["price_value"] = variants.effective(article, "selling_price")
                items.append(item)
        items = sort_articles(items, *(chosen or ("number", "asc")))
        return render(
            request,
            "articles.html",
            "nav.articles",
            articles=items,
            show_inactive=show_inactive,
            purged=purged == "1",
            sort_links=sort_links(chosen, show_inactive),
            toggle_inactive_href=list_url(chosen, not show_inactive),
        )

    def form_page(request: Request, status_code: int = 200, **context) -> HTMLResponse:
        """The article form: the whole page, or only the form for the pop-up. Both come from
        the same template, so they cannot drift apart."""
        response = render(
            request,
            "article_form.html",
            "nav.articles",
            status_code=status_code,
            popup=wants_popup(request),
            **context,
        )
        # The same address gives a different answer for HTMX, so caches must tell them apart.
        response.headers["Vary"] = "HX-Request"
        return response

    @app.get("/articles/new", response_class=HTMLResponse, dependencies=edit)
    def new_article_page(request: Request) -> HTMLResponse:
        return form_page(request, article=None, values={}, errors={})

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
                if variants.template_number_taken(db, number_key(values["number"])):
                    return {"number": "article.error.template_number"}, None
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
            return form_page(request, 422, article=None, values=raw, errors=errors)
        if wants_popup(request):
            return saved_in_popup()
        # The address is built from the stored article, not from the request.
        return RedirectResponse(f"/articles/{int(article_id)}", status_code=303)

    @app.get("/articles/{article_id}", response_class=HTMLResponse, dependencies=view)
    def article_detail(request: Request, article_id: int, saved: str | None = None) -> HTMLResponse:
        article = load(article_id)
        counts = {section: 0 for section in SECTIONS}
        with new_session() as db:
            counts["stock"] = stock.count_movements(db, article_id)
            blocked = article_purge.blocking(db, article_id)
        return render(
            request,
            "article_detail.html",
            "nav.articles",
            article=article,
            sections=SECTIONS,
            counts=counts,
            saved=saved == "1",
            can_purge=blocked,
            can_delete=not blocked,
        )

    @app.get("/articles/{article_id}/edit", response_class=HTMLResponse, dependencies=edit)
    def edit_article_page(request: Request, article_id: int) -> HTMLResponse:
        with new_session() as db:
            article = db.get(Article, article_id)
            if article is None:
                raise HTTPException(status_code=404)
            values = form_values(_stored_values(article), language)
            current = article_view(article)
            fields = _attribute_fields(db, article)
            values.update(variants.selected_values(article))
        return form_page(request, article=current, values=values, errors={}, attr_fields=fields)

    def attribute_fields_of(article_id: int) -> list[dict]:
        """The attribute fields of the article's template; empty for a normal article."""
        with new_session() as db:
            article = db.get(Article, article_id)
            if article is None:
                raise HTTPException(status_code=404)
            return _attribute_fields(db, article)

    def change(
        article_id: int, actor_id: int, raw: dict[str, str], fields: list[dict]
    ) -> tuple[dict[str, str], dict]:
        values, errors = validate(raw)
        try:
            with new_session() as db, db.begin():
                article = db.get(Article, article_id)
                if article is None:
                    raise HTTPException(status_code=404)
                current = article_view(article)
                choices = None
                if fields:
                    old_choices = variants.chosen(article)
                    # A value that is missing since the template got a new attribute may stay so.
                    missing = frozenset(f["id"] for f in fields if f["id"] not in old_choices)
                    choices, choice_errors = variants.check_choices(raw, fields, missing)
                    errors.update(choice_errors)
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
                if number_key(values["number"]) != article.number_key and (
                    variants.template_number_taken(db, number_key(values["number"]))
                ):
                    return {"number": "article.error.template_number"}, current
                if choices is not None and variants.combination_taken(
                    db, article.template_id, choices, article.id
                ):
                    return {"combination": "variant.error.combination_taken"}, current
                details = audit.field_changes(_logged(_stored_values(article)), _logged(values))
                if choices is not None:
                    details += variants.value_changes(
                        variants.describe(db, old_choices), variants.describe(db, choices)
                    )
                if details:
                    for name, value in values.items():
                        setattr(article, name, value)
                    article.number_key = number_key(article.number)
                    if choices is not None:
                        variants.apply_choices(db, article, choices)
                    db.flush()
                    audit.record_change(db, actor_id, "article.changed", article.number, details)
                return {}, article_view(article)
        except IntegrityError:
            return {"number": "article.error.taken"}, load(article_id)

    @app.post("/articles/{article_id}", response_class=HTMLResponse, dependencies=edit)
    async def save_article(request: Request, article_id: int) -> Response:
        fields = await run_in_threadpool(attribute_fields_of, article_id)
        names = FIELD_NAMES + tuple(variants.field_name(field["id"]) for field in fields)
        raw = await read_form(request, names)
        errors, article = await run_in_threadpool(
            change, article_id, request.state.user.id, raw, fields
        )
        if errors:
            return form_page(
                request, 422, article=article, values=raw, errors=errors, attr_fields=fields
            )
        if wants_popup(request):
            return saved_in_popup()
        return RedirectResponse(f"/articles/{int(article['id'])}?saved=1", status_code=303)

    def reset_to_template(article_id: int, actor_id: int, field: str) -> int:
        with new_session() as db, db.begin():
            article = db.get(Article, article_id)
            # Only a variant has a template value to go back to, and only for these fields.
            if (
                article is None
                or article.template_id is None
                or field not in variants.INHERITED_FIELDS
            ):
                raise HTTPException(status_code=404)
            old = getattr(article, field)
            if old is not None:
                setattr(article, field, None)
                db.flush()
                audit.record_change(
                    db,
                    actor_id,
                    "article.changed",
                    article.number,
                    [{"field": field, "old": str(old), "new": ""}],
                )
            return article.id

    @app.post("/articles/{article_id}/reset/{field}", dependencies=edit)
    async def reset_field(request: Request, article_id: int, field: str) -> Response:
        stored_id = await run_in_threadpool(
            reset_to_template, article_id, request.state.user.id, field
        )
        return RedirectResponse(f"/articles/{int(stored_id)}", status_code=303)

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

    def delete_view(article_id: int) -> tuple[dict, dict[str, int]]:
        """The article and its counts; 404 when it is gone, 409 when productions, stock
        movements or sales hang on it (then it can only be deactivated)."""
        with new_session() as db:
            article = db.get(Article, article_id)
            if article is None:
                raise HTTPException(status_code=404)
            if article_purge.blocking(db, article_id):
                raise HTTPException(status_code=409)
            return article_view(article), article_purge.counts(db, article_id)

    @app.get("/articles/{article_id}/delete", response_class=HTMLResponse, dependencies=edit)
    def delete_article_page(request: Request, article_id: int) -> HTMLResponse:
        article, found = delete_view(article_id)
        return render(
            request,
            "article_delete.html",
            "nav.articles",
            article=article,
            counts=found,
            count_keys=article_purge.COUNT_KEYS,
        )

    def do_delete(article_id: int, actor_id: int) -> None:
        try:
            with new_session() as db, db.begin():
                article = db.get(Article, article_id)
                if article is None:
                    raise HTTPException(status_code=404)
                if article_purge.blocking(db, article_id):
                    raise HTTPException(status_code=409)
                article_purge.remove(db, actor_id, article)
        except IntegrityError:
            # A booking arrived at the same moment: the database refused, nothing is deleted.
            raise HTTPException(status_code=409) from None

    @app.post("/articles/{article_id}/delete", dependencies=edit)
    async def delete_article(request: Request, article_id: int) -> Response:
        await run_in_threadpool(do_delete, article_id, request.state.user.id)
        return RedirectResponse("/articles", status_code=303)

    def purge_view(article_id: int) -> tuple[dict, dict[str, int]]:
        """The article and its counts; 404 when it is gone or nothing hangs on it."""
        with new_session() as db:
            article = db.get(Article, article_id)
            if article is None or not article_purge.blocking(db, article_id):
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
            if article is None or not article_purge.blocking(db, article_id):
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
