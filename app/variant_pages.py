"""Creating a variant of a template (issue #49).

A variant is a full article (own number, own stock) that points to a template and has one
value for each attribute of that template. Inheriting and editing are part of the article
pages; this module only holds the form for a new variant.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, auth, variants
from app.article_form import FIELD_NAMES, number_key, validate
from app.models import Article, ArticleAttributeValue, ArticleTemplate
from app.permissions import Permission, requires


def add_variant_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
) -> None:
    edit = [requires(Permission.EDIT)]

    def usable_template(db: Session, template_id: int) -> ArticleTemplate:
        """404 when there is no such template; 409 when it takes no new variants (it is
        deactivated, or has no attributes to tell variants apart)."""
        template = db.get(ArticleTemplate, template_id)
        if template is None:
            raise HTTPException(status_code=404)
        if not template.active or not variants.template_attributes(db, template_id):
            raise HTTPException(status_code=409)
        return template

    def load(template_id: int) -> tuple[dict, list[dict]]:
        with new_session() as db:
            template = usable_template(db, template_id)
            fields = variants.attribute_fields(db, template_id)
            return {
                "id": template.id,
                "name": template.name,
                "number": template.number,
            }, fields

    def form_page(request: Request, template: dict, fields, values, errors, status_code=200):
        return render(
            request,
            "variant_form.html",
            "nav.articles",
            status_code=status_code,
            tpl=template,
            attr_fields=fields,
            values=values,
            errors=errors,
        )

    @app.get(
        "/templates/{template_id}/variants/new", response_class=HTMLResponse, dependencies=edit
    )
    def new_variant_page(request: Request, template_id: int) -> HTMLResponse:
        template, fields = load(template_id)
        # The number is completed in the page from the chosen values (see app.js).
        return form_page(request, template, fields, {"name": template["name"]}, {})

    def create(
        actor_id: int, template_id: int, raw: dict[str, str], fields: list[dict]
    ) -> tuple[dict[str, str], int | None]:
        values, errors = validate(raw)
        choices, choice_errors = variants.check_choices(raw, fields)
        errors.update(choice_errors)
        if errors:
            return errors, None
        try:
            with new_session() as db, db.begin():
                template = usable_template(db, template_id)
                if db.scalar(
                    select(Article.id).where(Article.number_key == number_key(values["number"]))
                ):
                    return {"number": "article.error.taken"}, None
                if variants.combination_taken(db, template_id, choices):
                    return {"combination": "variant.error.combination_taken"}, None
                article = Article(
                    **values,
                    number_key=number_key(values["number"]),
                    active=True,
                    created_at=auth.utcnow(),
                    template_id=template_id,
                    combo_key=variants.combo_key(choices),
                )
                article.attribute_values = [
                    ArticleAttributeValue(attribute_id=attribute_id, value_id=value_id)
                    for attribute_id, value_id in choices.items()
                ]
                db.add(article)
                db.flush()
                details = [{"field": "template", "old": "", "new": template.name}] + [
                    {"field": "attribute_value", "old": "", "new": text}
                    for text in variants.describe(db, choices).values()
                ]
                audit.record_change(
                    db, actor_id, "article.variant_created", article.number, details
                )
                return {}, article.id
        except IntegrityError:
            # Two saves at the same moment: the database decides which number or combination
            # was first.
            with new_session() as db:
                if db.scalar(
                    select(Article.id).where(Article.number_key == number_key(values["number"]))
                ):
                    return {"number": "article.error.taken"}, None
            return {"combination": "variant.error.combination_taken"}, None

    @app.post(
        "/templates/{template_id}/variants/new", response_class=HTMLResponse, dependencies=edit
    )
    async def create_variant(request: Request, template_id: int) -> Response:
        template, fields = await run_in_threadpool(load, template_id)
        names = FIELD_NAMES + tuple(variants.field_name(field["id"]) for field in fields)
        raw = await read_form(request, names)
        errors, article_id = await run_in_threadpool(
            create, request.state.user.id, template_id, raw, fields
        )
        if errors:
            return form_page(request, template, fields, raw, errors, status_code=422)
        # The address is built from the stored article, not from the request.
        return RedirectResponse(f"/articles/{int(article_id)}", status_code=303)
