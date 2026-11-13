"""Creating all variants of a template at once (issue #50).

The page first shows a preview: one row per combination of attribute values, with a suggested
article number that can be changed. Combinations that already exist are marked and skipped.
Saving is all or nothing: one wrong number and nothing is stored.
"""

import itertools
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, auth, variants
from app.article_form import NAME_MAX_LENGTH, number_key, validate
from app.models import Article, ArticleAttributeValue, ArticleTemplate
from app.permissions import Permission, requires

# More combinations than this are not shown or created in one go.
MAX_COMBINATIONS = 200


def number_field(index: int) -> str:
    return f"number_{index}"


def combinations(template: ArticleTemplate, fields: list[dict], existing: set[str]) -> list[dict]:
    """All combinations of values in the order of the attributes; flags those that exist."""
    rows = []
    for combo in itertools.product(*(field["options"] for field in fields)):
        choices = {field["id"]: option["id"] for field, option in zip(fields, combo, strict=True)}
        labels = [option["value"] for option in combo]
        parts = [option["abbreviation"] for option in combo if option["abbreviation"]]
        suggestion = "-".join([template.number, *parts]) if template.number else ""
        rows.append(
            {
                "index": len(rows),
                "choices": choices,
                "labels": labels,
                "name": f"{template.name} {' '.join(labels)}"[:NAME_MAX_LENGTH],
                "suggestion": suggestion[:50],
                "present": variants.combo_key(choices) in existing,
            }
        )
    return rows


def add_variant_bulk_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
) -> None:
    edit = [requires(Permission.EDIT)]

    def load(db: Session, template_id: int) -> tuple[ArticleTemplate, list[dict], list[dict]]:
        template = db.get(ArticleTemplate, template_id)
        if template is None:
            raise HTTPException(status_code=404)
        fields = variants.attribute_fields(db, template_id)
        # A template that is deactivated, has no attributes or an attribute without values
        # has nothing to combine.
        if not template.active or not fields or any(not f["options"] for f in fields):
            raise HTTPException(status_code=409)
        total = 1
        for field in fields:
            total *= len(field["options"])
        if total > MAX_COMBINATIONS:
            raise HTTPException(status_code=409)
        existing = set(variants.existing_combo_keys(db, template_id))
        return template, fields, combinations(template, fields, existing)

    def page(request: Request, template: dict, fields, rows, values, errors, status_code=200):
        return render(
            request,
            "variant_bulk.html",
            "nav.articles",
            status_code=status_code,
            tpl=template,
            attr_fields=fields,
            rows=rows,
            values=values,
            errors=errors,
            new_count=sum(1 for row in rows if not row["present"]),
        )

    def view(template_id: int) -> tuple[dict, list[dict], list[dict]]:
        with new_session() as db:
            template, fields, rows = load(db, template_id)
            return {"id": template.id, "name": template.name}, fields, rows

    @app.get(
        "/templates/{template_id}/variants/all", response_class=HTMLResponse, dependencies=edit
    )
    def preview(request: Request, template_id: int) -> HTMLResponse:
        template, fields, rows = view(template_id)
        values = {number_field(row["index"]): row["suggestion"] for row in rows}
        return page(request, template, fields, rows, values, {})

    def check_numbers(db: Session, rows: list[dict], raw: dict[str, str]):
        """Returns the cleaned numbers by row index and the errors by field name."""
        numbers: dict[int, str] = {}
        errors: dict[str, str] = {}
        seen: set[str] = set()
        for row in rows:
            if row["present"]:
                continue
            field = number_field(row["index"])
            cleaned, row_errors = validate({"number": raw.get(field, ""), "name": row["name"]})
            if "number" in row_errors:
                errors[field] = row_errors["number"]
                continue
            key = number_key(cleaned["number"])
            if key in seen or db.scalar(select(Article.id).where(Article.number_key == key)):
                errors[field] = "article.error.taken"
            elif variants.template_number_taken(db, key):
                errors[field] = "article.error.template_number"
            seen.add(key)
            numbers[row["index"]] = cleaned["number"]
        return numbers, errors

    def create(actor_id: int, template_id: int, raw: dict[str, str]):
        """Returns the errors, or no errors and the number of created articles."""
        try:
            with new_session() as db, db.begin():
                template, _fields, rows = load(db, template_id)
                numbers, errors = check_numbers(db, rows, raw)
                if errors:
                    return errors, 0
                for row in rows:
                    if row["present"]:
                        continue
                    number = numbers[row["index"]]
                    article = Article(
                        number=number,
                        name=row["name"],
                        number_key=number_key(number),
                        selling_price=None,
                        weight=None,
                        min_stock=None,
                        active=True,
                        created_at=auth.utcnow(),
                        template_id=template_id,
                        combo_key=variants.combo_key(row["choices"]),
                    )
                    article.attribute_values = [
                        ArticleAttributeValue(attribute_id=a, value_id=v)
                        for a, v in row["choices"].items()
                    ]
                    db.add(article)
                    db.flush()
                    details = [{"field": "template", "old": "", "new": template.name}] + [
                        {"field": "attribute_value", "old": "", "new": text}
                        for text in variants.describe(db, row["choices"]).values()
                    ]
                    audit.record_change(
                        db, actor_id, "article.variant_created", article.number, details
                    )
                return {}, len(numbers)
        except IntegrityError:
            # Two saves at the same moment: look again, the database decided who was first.
            with new_session() as db:
                _template, _fields, rows = load(db, template_id)
                _numbers, errors = check_numbers(db, rows, raw)
            return errors or {"combination": "variant.error.combination_taken"}, 0

    @app.post(
        "/templates/{template_id}/variants/all", response_class=HTMLResponse, dependencies=edit
    )
    async def create_all(request: Request, template_id: int) -> Response:
        template, fields, rows = await run_in_threadpool(view, template_id)
        names = tuple(number_field(row["index"]) for row in rows)
        raw = await read_form(request, names)
        errors, _count = await run_in_threadpool(create, request.state.user.id, template_id, raw)
        if errors:
            return page(request, template, fields, rows, raw, errors, status_code=422)
        return RedirectResponse(f"/templates/{int(template_id)}?saved=1", status_code=303)
