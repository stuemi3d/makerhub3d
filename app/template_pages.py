"""Attributes and article templates (issue #44, part 1).

An attribute is a feature such as colour with its values; several templates can use it.
A template describes a family of articles: name, description, default values and attributes.
It is not an article: it has no stock and no sales. Variants come with a later part of #44.
Both can be deleted after a confirmation as long as nothing hangs on them; templates can
also be deactivated (plan, section 4).
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, auth
from app.article_form import NAME_MAX_LENGTH, form_values, validate_defaults
from app.models import ArticleTemplate, Attribute, AttributeValue, TemplateAttribute
from app.permissions import Permission, requires
from app.settings_form import format_decimal

NAME_LIMIT = 50
VALUE_LIMIT = 50
DESCRIPTION_LIMIT = 1000
TEMPLATE_FIELDS = ("name", "description", "selling_price", "weight", "min_stock")
DEFAULT_FIELDS = ("selling_price", "weight", "min_stock")


def key_of(text: str) -> str:
    """The form of a name used to compare them (case does not matter)."""
    return text.casefold()


def _clean_line(text: str) -> str:
    return text.strip()


def _printable(text: str) -> bool:
    return all(char.isprintable() for char in text)


def validate_attribute_name(raw: str) -> tuple[str | None, str | None]:
    name = _clean_line(raw)
    if not name:
        return None, "attribute.error.required"
    if len(name) > NAME_LIMIT:
        return None, "attribute.error.too_long"
    if not _printable(name):
        return None, "attribute.error.chars"
    return name, None


def validate_attribute_values(raw: str) -> tuple[list[str], str | None]:
    """One value per line; empty lines are ignored. Returns the values or an error key."""
    values: list[str] = []
    for line in raw.splitlines():
        value = _clean_line(line)
        if not value:
            continue
        if len(value) > VALUE_LIMIT:
            return [], "attribute.error.too_long"
        if not _printable(value):
            return [], "attribute.error.chars"
        values.append(value)
    if not values:
        return [], "attribute.error.values_required"
    if len({key_of(value) for value in values}) != len(values):
        return [], "attribute.error.value_taken"
    return values, None


def validate_template(raw: dict[str, str]) -> tuple[dict[str, object], dict[str, str]]:
    values: dict[str, object] = {}
    errors: dict[str, str] = {}

    name = raw.get("name", "").strip()
    if not name:
        errors["name"] = "article.error.required"
    elif len(name) > NAME_MAX_LENGTH:
        errors["name"] = "article.error.too_long"
    elif not _printable(name):
        errors["name"] = "article.error.chars"
    else:
        values["name"] = name

    # A description may have several lines.
    description = raw.get("description", "").replace("\r\n", "\n").strip()
    if len(description) > DESCRIPTION_LIMIT:
        errors["description"] = "article.error.too_long"
    elif not all(char.isprintable() or char == "\n" for char in description):
        errors["description"] = "article.error.chars"
    else:
        values["description"] = description

    default_values, default_errors = validate_defaults(raw)
    values.update(default_values)
    errors.update(default_errors)
    return values, errors


def add_template_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
    language: str,
) -> None:
    view = [requires(Permission.VIEW)]
    edit = [requires(Permission.EDIT)]

    def shown(value: Decimal | None) -> str | None:
        return None if value is None else format_decimal(value.quantize(Decimal("0.01")), language)

    # ----- attributes -----------------------------------------------------------------------

    def attribute_view(db: Session, attribute: Attribute) -> dict:
        used = db.scalar(
            select(TemplateAttribute.template_id)
            .where(TemplateAttribute.attribute_id == attribute.id)
            .limit(1)
        )
        return {
            "id": attribute.id,
            "name": attribute.name,
            "value_list": [value.value for value in attribute.values_rel],
            "used": used is not None,
        }

    def attribute_or_404(db: Session, attribute_id: int) -> Attribute:
        attribute = db.get(Attribute, attribute_id)
        if attribute is None:
            raise HTTPException(status_code=404)
        return attribute

    def attribute_list(request: Request, errors: dict | None = None, status_code: int = 200):
        with new_session() as db:
            items = [
                attribute_view(db, attribute)
                for attribute in db.scalars(select(Attribute).order_by(Attribute.name_key))
            ]
        return render(
            request,
            "attributes.html",
            "nav.articles",
            status_code=status_code,
            attributes=items,
            errors=errors or {},
        )

    @app.get("/attributes", response_class=HTMLResponse, dependencies=view)
    def attributes_page(request: Request) -> HTMLResponse:
        return attribute_list(request)

    @app.get("/attributes/new", response_class=HTMLResponse, dependencies=edit)
    def new_attribute_page(request: Request) -> HTMLResponse:
        return render(request, "attribute_form.html", "nav.articles", values={}, errors={})

    def create_attribute(actor_id: int, raw: dict[str, str]) -> dict[str, str]:
        name, name_error = validate_attribute_name(raw.get("name", ""))
        values, values_error = validate_attribute_values(raw.get("values", ""))
        errors = {}
        if name_error:
            errors["name"] = name_error
        if values_error:
            errors["values"] = values_error
        if errors:
            return errors
        try:
            with new_session() as db, db.begin():
                if db.scalar(select(Attribute.id).where(Attribute.name_key == key_of(name))):
                    return {"name": "attribute.error.taken"}
                attribute = Attribute(name=name, name_key=key_of(name))
                attribute.values_rel = [
                    AttributeValue(value=value, value_key=key_of(value)) for value in values
                ]
                db.add(attribute)
                db.flush()
                audit.record_change(db, actor_id, "attribute.created", attribute.name)
        except IntegrityError:
            return {"name": "attribute.error.taken"}
        return {}

    @app.post("/attributes/new", response_class=HTMLResponse, dependencies=edit)
    async def create_attribute_route(request: Request) -> Response:
        raw = await read_form(request, ("name", "values"))
        errors = await run_in_threadpool(create_attribute, request.state.user.id, raw)
        if errors:
            return render(
                request,
                "attribute_form.html",
                "nav.articles",
                status_code=422,
                values=raw,
                errors=errors,
            )
        return RedirectResponse("/attributes", status_code=303)

    def add_value(attribute_id: int, actor_id: int, raw: str) -> str | None:
        value = _clean_line(raw)
        error = None
        if not value:
            error = "attribute.error.value_required"
        elif len(value) > VALUE_LIMIT:
            error = "attribute.error.too_long"
        elif not _printable(value):
            error = "attribute.error.chars"
        try:
            with new_session() as db, db.begin():
                attribute = attribute_or_404(db, attribute_id)
                if error:
                    return error
                if any(key_of(v.value) == key_of(value) for v in attribute.values_rel):
                    return "attribute.error.value_taken"
                attribute.values_rel.append(AttributeValue(value=value, value_key=key_of(value)))
                db.flush()
                audit.record_change(
                    db,
                    actor_id,
                    "attribute.value_added",
                    attribute.name,
                    [{"field": "value", "old": "", "new": value}],
                )
        except IntegrityError:
            return "attribute.error.value_taken"
        return None

    @app.post("/attributes/{attribute_id}/values", dependencies=edit)
    async def add_attribute_value(request: Request, attribute_id: int) -> Response:
        raw = await read_form(request, ("value",))
        error = await run_in_threadpool(
            add_value, attribute_id, request.state.user.id, raw.get("value", "")
        )
        if error:
            return attribute_list(request, errors={attribute_id: error}, status_code=422)
        return RedirectResponse("/attributes", status_code=303)

    @app.get("/attributes/{attribute_id}/delete", response_class=HTMLResponse, dependencies=edit)
    def delete_attribute_page(request: Request, attribute_id: int) -> HTMLResponse:
        with new_session() as db:
            attribute = attribute_view(db, attribute_or_404(db, attribute_id))
        return render(request, "attribute_delete.html", "nav.articles", attribute=attribute)

    def delete_attribute(attribute_id: int, actor_id: int) -> None:
        with new_session() as db, db.begin():
            attribute = attribute_or_404(db, attribute_id)
            if attribute_view(db, attribute)["used"]:
                raise HTTPException(status_code=409)
            name = attribute.name
            db.delete(attribute)
            audit.record_change(db, actor_id, "attribute.deleted", name)

    @app.post("/attributes/{attribute_id}/delete", dependencies=edit)
    async def delete_attribute_route(request: Request, attribute_id: int) -> Response:
        await run_in_threadpool(delete_attribute, attribute_id, request.state.user.id)
        return RedirectResponse("/attributes", status_code=303)

    # ----- templates ------------------------------------------------------------------------

    def template_attributes(db: Session, template_id: int) -> list[dict]:
        rows = db.execute(
            select(Attribute)
            .join(TemplateAttribute, TemplateAttribute.attribute_id == Attribute.id)
            .where(TemplateAttribute.template_id == template_id)
            .order_by(TemplateAttribute.position)
        )
        return [attribute_view(db, attribute) for (attribute,) in rows]

    def template_view(db: Session, template: ArticleTemplate) -> dict:
        return {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "selling_price": shown(template.selling_price),
            "weight": shown(template.weight),
            "min_stock": template.min_stock,
            "active": template.active,
            "attributes": template_attributes(db, template.id),
        }

    def template_or_404(db: Session, template_id: int) -> ArticleTemplate:
        template = db.get(ArticleTemplate, template_id)
        if template is None:
            raise HTTPException(status_code=404)
        return template

    def all_attributes() -> list[dict]:
        with new_session() as db:
            return [
                {"id": attribute.id, "name": attribute.name}
                for attribute in db.scalars(select(Attribute).order_by(Attribute.name_key))
            ]

    def chosen_ids(raw: dict[str, str], known: list[dict]) -> list[int]:
        """The checked attributes, in the order of their ids; unknown ids are ignored."""
        return sorted(item["id"] for item in known if raw.get(f"attr_{item['id']}"))

    def read_template_form(request: Request, known: list[dict]):
        names = TEMPLATE_FIELDS + tuple(f"attr_{item['id']}" for item in known)
        return read_form(request, names)

    def form_page(request: Request, template, values, errors, status_code=200) -> HTMLResponse:
        return render(
            request,
            "template_form.html",
            "nav.articles",
            status_code=status_code,
            tpl=template,
            values=values,
            errors=errors,
            all_attributes=all_attributes(),
        )

    @app.get("/templates", response_class=HTMLResponse, dependencies=view)
    def template_list(request: Request, inactive: str | None = None) -> HTMLResponse:
        show_inactive = inactive == "1"
        with new_session() as db:
            query = select(ArticleTemplate).order_by(ArticleTemplate.name)
            if not show_inactive:
                query = query.where(ArticleTemplate.active.is_(True))
            items = [template_view(db, template) for template in db.scalars(query)]
        return render(
            request,
            "templates_list.html",
            "nav.articles",
            templates=items,
            show_inactive=show_inactive,
        )

    @app.get("/templates/new", response_class=HTMLResponse, dependencies=edit)
    def new_template_page(request: Request) -> HTMLResponse:
        return form_page(request, None, {}, {})

    def attach(db: Session, template_id: int, attribute_ids: list[int]) -> None:
        for position, attribute_id in enumerate(attribute_ids):
            db.add(
                TemplateAttribute(
                    template_id=template_id, attribute_id=attribute_id, position=position
                )
            )

    def create_template(actor_id: int, raw: dict[str, str], known: list[dict]):
        values, errors = validate_template(raw)
        if errors:
            return errors, None
        with new_session() as db, db.begin():
            template = ArticleTemplate(**values, active=True, created_at=auth.utcnow())
            db.add(template)
            db.flush()
            # Only attributes that exist are attached.
            existing = set(db.scalars(select(Attribute.id)))
            attach(db, template.id, [i for i in chosen_ids(raw, known) if i in existing])
            db.flush()
            audit.record_change(db, actor_id, "template.created", template.name)
            return {}, template.id

    @app.post("/templates/new", response_class=HTMLResponse, dependencies=edit)
    async def create_template_route(request: Request) -> Response:
        known = await run_in_threadpool(all_attributes)
        raw = await read_template_form(request, known)
        errors, template_id = await run_in_threadpool(
            create_template, request.state.user.id, raw, known
        )
        if errors:
            return form_page(request, None, raw, errors, status_code=422)
        # The address is built from the stored template, not from the request.
        return RedirectResponse(f"/templates/{int(template_id)}", status_code=303)

    @app.get("/templates/{template_id}", response_class=HTMLResponse, dependencies=view)
    def template_detail(
        request: Request, template_id: int, saved: str | None = None
    ) -> HTMLResponse:
        with new_session() as db:
            template = template_view(db, template_or_404(db, template_id))
        return render(
            request, "template_detail.html", "nav.articles", tpl=template, saved=saved == "1"
        )

    @app.get("/templates/{template_id}/edit", response_class=HTMLResponse, dependencies=edit)
    def edit_template_page(request: Request, template_id: int) -> HTMLResponse:
        with new_session() as db:
            template = template_or_404(db, template_id)
            values = form_values(
                {name: getattr(template, name) for name in DEFAULT_FIELDS}, language
            )
            values["name"] = template.name
            values["description"] = template.description
            for item in template_attributes(db, template.id):
                values[f"attr_{item['id']}"] = "1"
            current = template_view(db, template)
        return form_page(request, current, values, {})

    def change_template(template_id: int, actor_id: int, raw: dict[str, str], known: list[dict]):
        values, errors = validate_template(raw)
        with new_session() as db, db.begin():
            template = template_or_404(db, template_id)
            current = template_view(db, template)
            if errors:
                return errors, current
            old_names = ", ".join(item["name"] for item in current["attributes"])
            old = {name: getattr(template, name) for name in TEMPLATE_FIELDS}
            existing = set(db.scalars(select(Attribute.id)))
            ids = [i for i in chosen_ids(raw, known) if i in existing]
            db.query(TemplateAttribute).filter_by(template_id=template.id).delete()
            attach(db, template.id, ids)
            for name, value in values.items():
                setattr(template, name, value)
            db.flush()
            new_names = ", ".join(item["name"] for item in template_attributes(db, template.id))
            changes = audit.field_changes(
                {k: "" if v is None else v for k, v in old.items()} | {"attributes": old_names},
                {k: "" if v is None else v for k, v in values.items()} | {"attributes": new_names},
            )
            if changes:
                audit.record_change(db, actor_id, "template.changed", template.name, changes)
            return {}, template_view(db, template)

    @app.post("/templates/{template_id}", response_class=HTMLResponse, dependencies=edit)
    async def save_template(request: Request, template_id: int) -> Response:
        known = await run_in_threadpool(all_attributes)
        raw = await read_template_form(request, known)
        errors, template = await run_in_threadpool(
            change_template, template_id, request.state.user.id, raw, known
        )
        if errors:
            return form_page(request, template, raw, errors, status_code=422)
        return RedirectResponse(f"/templates/{int(template['id'])}?saved=1", status_code=303)

    def set_active(template_id: int, actor_id: int, active: bool) -> int:
        with new_session() as db, db.begin():
            template = template_or_404(db, template_id)
            if template.active != active:
                template.active = active
                action = "template.activated" if active else "template.deactivated"
                audit.record_change(db, actor_id, action, template.name)
            return template.id

    @app.post("/templates/{template_id}/deactivate", dependencies=edit)
    async def deactivate_template(request: Request, template_id: int) -> Response:
        stored_id = await run_in_threadpool(set_active, template_id, request.state.user.id, False)
        return RedirectResponse(f"/templates/{int(stored_id)}", status_code=303)

    @app.post("/templates/{template_id}/activate", dependencies=edit)
    async def activate_template(request: Request, template_id: int) -> Response:
        stored_id = await run_in_threadpool(set_active, template_id, request.state.user.id, True)
        return RedirectResponse(f"/templates/{int(stored_id)}", status_code=303)

    @app.get("/templates/{template_id}/delete", response_class=HTMLResponse, dependencies=edit)
    def delete_template_page(request: Request, template_id: int) -> HTMLResponse:
        with new_session() as db:
            template = template_view(db, template_or_404(db, template_id))
        return render(request, "template_delete.html", "nav.articles", tpl=template)

    def delete_template(template_id: int, actor_id: int) -> None:
        with new_session() as db, db.begin():
            template = template_or_404(db, template_id)
            name = template.name
            db.delete(template)
            audit.record_change(db, actor_id, "template.deleted", name)

    @app.post("/templates/{template_id}/delete", dependencies=edit)
    async def delete_template_route(request: Request, template_id: int) -> Response:
        await run_in_threadpool(delete_template, template_id, request.state.user.id)
        return RedirectResponse("/templates", status_code=303)
