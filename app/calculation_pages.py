"""Pages of the calculation (issues #161, #186).

One list of all calculations (or of one article), like the productions. New, edit, copy, view
and delete open in the pop-up; without JavaScript they are pages of their own. Calculating is
open to everybody who may look; saving, editing, copying, marking and deleting need the right to
edit. A calculation lives under its own number, its article follows from it.
"""

import re
from collections.abc import Callable
from urllib.parse import quote

import httpx2
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import (
    calc_form,
    calc_store,
    printer_models,
    production_form,
    spool_list,
    stock,
    variants,
)
from app.article_pages import confirmed, varied, wants_popup
from app.models import AppSettings, Article, Calculation, Device
from app.permissions import Permission, has_permission, requires
from app.two_factor import Cipher

MAX_ID = 2**31 - 1
_ID = re.compile(r"[0-9]{1,10}")
LIST = "/calculation"


def number(text: str) -> int:
    """An id from the address or a form; anything else (or out of range) is "not found"."""
    if not _ID.fullmatch(text) or not 0 < int(text) <= MAX_ID:
        raise HTTPException(status_code=404)
    return int(text)


def back_to_list(article: Article, filter_text: str, flag: str) -> str:
    """The address of the list after a change. A filter the person came with stays only when
    it is the article of the calculation; the address is built from the stored article."""
    if _ID.fullmatch(filter_text or "") and int(filter_text) == article.id:
        return f"{LIST}?article={int(article.id)}&{flag}=1"
    return f"{LIST}?{flag}=1"


def add_calculation_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    render: Callable[..., HTMLResponse],
    language: str,
) -> None:
    view_right = [requires(Permission.VIEW)]
    edit_right = [requires(Permission.EDIT)]

    def article_of(db: Session, article_id: int) -> Article:
        article = db.get(Article, article_id) if 0 < article_id <= MAX_ID else None
        if article is None:
            raise HTTPException(status_code=404)
        return article

    def stored_of(db: Session, calculation_id: int) -> Calculation:
        item = db.get(Calculation, calculation_id) if 0 < calculation_id <= MAX_ID else None
        if item is None:
            raise HTTPException(status_code=404)
        return item

    def can_edit(request: Request) -> bool:
        return has_permission(request.state.user.role, Permission.EDIT)

    # --- The list ---------------------------------------------------------------------------

    @app.get(LIST, response_class=HTMLResponse, dependencies=view_right)
    def calculation_list(
        request: Request,
        article: str | None = None,
        saved: str | None = None,
        main: str | None = None,
        deleted: str | None = None,
        sort: str | None = None,
        direction: str | None = Query(None, alias="dir"),
    ) -> HTMLResponse:
        filtered = None
        with new_session() as db:
            article_id = number(article.strip()) if article else None
            if article_id is not None:
                found = article_of(db, article_id)
                filtered = {"id": found.id, "number": found.number, "name": found.name}
            shown = calc_store.rows(db, language, article_id)
        keep = {"article": str(article_id)} if article_id is not None else None
        shown, links = calc_store.arranged(shown, LIST, sort, direction, keep)
        notice = "saved" if saved == "1" else "main" if main == "1" else None
        notice = notice or ("deleted" if deleted == "1" else None)
        return render(
            request,
            "calculation_list.html",
            "nav.calculation",
            rows=shown,
            sort_links=links,
            article=filtered,
            notice=notice,
        )

    # --- The form: calculate and save ---------------------------------------------------------

    def choices(db: Session, selected: int | None) -> list[dict]:
        query = select(Article).where(Article.active.is_(True)).order_by(Article.number_key)
        found = [{"id": a.id, "label": f"{a.number} · {a.name}"} for a in db.scalars(query)]
        if selected is not None and all(item["id"] != selected for item in found):
            chosen = db.get(Article, selected)
            if chosen is not None:
                found.append({"id": chosen.id, "label": f"{chosen.number} · {chosen.name}"})
        return found

    def page(
        request: Request,
        draft: calc_form.Draft,
        article_id: int | None,
        form_action: str,
        filter_text: str = "",
        status_code: int = 200,
        extra_errors: dict[str, str] | None = None,
        save_failed: bool = False,
        editing: bool = False,
        saving: bool = False,
    ) -> tuple[HTMLResponse, calc_form.Outcome]:
        article = None
        target = None
        with new_session() as db:
            if article_id is not None:
                found = article_of(db, article_id)
                article = {"id": found.id, "number": found.number, "name": found.name}
                target = variants.effective(found, "selling_price")
            article_choices = [] if editing else choices(db, article_id)
            printers = production_form.load_printers(db)
            settings = db.get(AppSettings, 1)
            tables = {}
            for device_id, _ in printers.choices:
                device = db.get(Device, device_id)
                tables[device_id] = (
                    device.printer_model_id is not None,
                    printer_models.power_table(db, device_id),
                )
        transport: httpx2.BaseTransport | None = getattr(app.state, "bambuddy_transport", None)
        spools = spool_list.load(new_session, cipher, transport)
        ctx = calc_form.Context(
            settings=settings,
            rates=printers.rates,
            tables=tables,
            spools=spools.spools,
            spool_problem=spools.problem,
            target_price=target,
            language=language,
            names=dict(printers.choices),
        )
        outcome = calc_form.evaluate(draft, ctx)
        outcome.errors.update(extra_errors or {})
        if saving and outcome.plan is None:
            # Nothing can be saved: the same form, with the notice
            status_code, save_failed = 422, True
        response = render(
            request,
            "calculation_new.html",
            "nav.calculation",
            status_code=status_code,
            popup=wants_popup(request),
            draft=draft,
            article=article,
            article_choices=article_choices,
            printers=printers.choices,
            outcome=outcome,
            errors=outcome.errors,
            can_add_row=len(draft.rows) < calc_form.MAX_ROWS,
            form_action=form_action,
            can_save=article is not None and can_edit(request),
            needs_article=article is None and can_edit(request),
            save_failed=save_failed,
            editing=editing,
            filter_text=filter_text if _ID.fullmatch(filter_text) else "",
        )
        return varied(response), outcome

    def open_page(request: Request) -> HTMLResponse:
        query = request.query_params
        article_text = query.get("article", "").strip()
        copy_text = query.get("copy", "").strip()
        filter_text = query.get("filter", "").strip() or article_text
        draft = calc_form.Draft()
        article_id = number(article_text) if article_text else None
        if copy_text:
            with new_session() as db:
                source = stored_of(db, number(copy_text))
                draft = calc_store.draft_of(source, language)
                article_id = source.article_id
        return page(request, draft, article_id, f"{LIST}/new", filter_text)[0]

    @app.get(f"{LIST}/new", response_class=HTMLResponse, dependencies=view_right)
    async def new_calculation(request: Request) -> HTMLResponse:
        return await run_in_threadpool(open_page, request)

    def work(
        request: Request,
        draft: calc_form.Draft,
        article_text: str,
        form_action: str,
        filter_text: str,
        item_id: int | None = None,
    ) -> Response:
        """Calculates again, or saves when the form asks for it."""
        article_id = number(article_text) if article_text else None
        editing = item_id is not None
        if draft.action != "save":
            return page(request, draft, article_id, form_action, filter_text, editing=editing)[0]
        if not can_edit(request):
            raise HTTPException(status_code=403)
        if article_id is None:
            response, _ = page(
                request,
                draft,
                None,
                form_action,
                filter_text,
                422,
                {"article": "calc.error.article"},
                save_failed=True,
                editing=editing,
            )
            return response
        response, outcome = page(
            request, draft, article_id, form_action, filter_text, editing=editing, saving=True
        )
        if outcome.plan is None:
            return response
        with new_session() as db, db.begin():
            article = article_of(db, article_id)
            if item_id is None:
                calc_store.create(db, request.state.user.id, article, outcome.plan, language)
            else:
                item = stored_of(db, item_id)
                calc_store.update(db, request.state.user.id, article, item, outcome.plan, language)
            target = back_to_list(article, filter_text, "saved")
        if wants_popup(request):
            return Response(status_code=204, headers={"HX-Redirect": target})
        return RedirectResponse(target, status_code=303)

    @app.post(f"{LIST}/new", response_class=HTMLResponse, dependencies=view_right)
    async def calculate(request: Request) -> Response:
        form = await request.form()
        article = form.get("article")
        filter_text = form.get("filter")
        draft = calc_form.read(form)
        return await run_in_threadpool(
            work,
            request,
            draft,
            article.strip() if isinstance(article, str) else "",
            f"{LIST}/new",
            filter_text.strip() if isinstance(filter_text, str) else "",
        )

    # --- One saved calculation ----------------------------------------------------------------

    @app.get(LIST + "/{calculation_id}", response_class=HTMLResponse, dependencies=view_right)
    def view_calculation(request: Request, calculation_id: int) -> HTMLResponse:
        with new_session() as db:
            item = stored_of(db, calculation_id)
            article = article_of(db, item.article_id)
            shown = {
                "id": item.id,
                "date": stock.format_date(item.created_on, language),
                "printer": item.printer_name,
                "is_main": item.is_main,
                "print_minutes": production_form.input_text(item.print_minutes, language),
                "pieces": item.pieces,
                "setup": production_form.input_text(item.setup_minutes, language),
                "rework": production_form.input_text(item.rework_minutes, language),
                "scrap": production_form.input_text(item.scrap_percent, language),
                "margin": production_form.input_text(item.margin_percent, language),
                "power_price": production_form.money_text(item.power_price, language),
                "machine_rate": production_form.money_text(item.machine_rate, language),
                "labour_rate": production_form.money_text(item.labour_rate, language),
                "result": {
                    "material": calc_form.cents(item.material_per_plate, language),
                    "power": calc_form.cents(item.power_per_plate, language),
                    "machine": calc_form.cents(item.machine_per_plate, language),
                    "labour": calc_form.cents(item.labour_cost, language),
                    "cost_without": calc_form.cents(item.cost_without_labour, language),
                    "cost_with": calc_form.cents(item.cost_with_labour, language),
                    "suggested": calc_form.cents(item.suggested_price, language),
                    "profit": calc_form.cents(item.profit, language),
                    "wage": (
                        calc_form.NO_VALUE
                        if item.hourly_wage is None
                        else calc_form.cents(item.hourly_wage, language)
                    ),
                },
                "multi": len(item.materials) > 1,
                "materials": calc_store.detail_materials(item, language),
            }
            article_view = {"id": article.id, "number": article.number, "name": article.name}
        return varied(
            render(
                request,
                "calculation_view.html",
                "nav.calculation",
                popup=wants_popup(request),
                article=article_view,
                item=shown,
            )
        )

    @app.get(LIST + "/{calculation_id}/edit", response_class=HTMLResponse, dependencies=edit_right)
    def edit_calculation_page(request: Request, calculation_id: int) -> HTMLResponse:
        filter_text = request.query_params.get("filter", "").strip()
        with new_session() as db:
            item = stored_of(db, calculation_id)
            draft = calc_store.draft_of(item, language)
            article_id = item.article_id
        action = edit_action(calculation_id, filter_text)
        return page(request, draft, article_id, action, filter_text, editing=True)[0]

    def edit_action(calculation_id: int, filter_text: str) -> str:
        suffix = f"?filter={quote(filter_text)}" if _ID.fullmatch(filter_text) else ""
        return f"{LIST}/{int(calculation_id)}/edit{suffix}"

    @app.post(LIST + "/{calculation_id}/edit", response_class=HTMLResponse, dependencies=edit_right)
    async def edit_calculation(request: Request, calculation_id: int) -> Response:
        with new_session() as db:
            article_id = stored_of(db, calculation_id).article_id
        filter_text = request.query_params.get("filter", "").strip()
        draft = calc_form.read(await request.form())
        return await run_in_threadpool(
            work,
            request,
            draft,
            str(article_id),
            edit_action(calculation_id, filter_text),
            filter_text,
            calculation_id,
        )

    @app.post(LIST + "/{calculation_id}/main", dependencies=edit_right)
    def main_calculation(request: Request, calculation_id: int) -> Response:
        with new_session() as db, db.begin():
            item = stored_of(db, calculation_id)
            article = article_of(db, item.article_id)
            calc_store.make_main(db, request.state.user.id, article, item, language)
            target = back_to_list(article, request.query_params.get("filter", "").strip(), "main")
        return RedirectResponse(target, status_code=303)

    @app.get(
        LIST + "/{calculation_id}/delete", response_class=HTMLResponse, dependencies=edit_right
    )
    def delete_calculation_page(request: Request, calculation_id: int) -> HTMLResponse:
        filter_text = request.query_params.get("filter", "").strip()
        with new_session() as db:
            item = stored_of(db, calculation_id)
            article = article_of(db, item.article_id)
            shown = {
                "id": item.id,
                "date": stock.format_date(item.created_on, language),
                "printer": item.printer_name,
                "article": f"{article.number} · {article.name}",
                "filter": filter_text if _ID.fullmatch(filter_text) else "",
            }
        return varied(
            render(
                request,
                "calculation_delete.html",
                "nav.calculation",
                popup=wants_popup(request),
                item=shown,
            )
        )

    @app.post(LIST + "/{calculation_id}/delete", dependencies=edit_right)
    def delete_calculation(request: Request, calculation_id: int) -> Response:
        with new_session() as db, db.begin():
            item = stored_of(db, calculation_id)
            article = article_of(db, item.article_id)
            calc_store.remove(db, request.state.user.id, article, item, language)
            target = back_to_list(
                article, request.query_params.get("filter", "").strip(), "deleted"
            )
        return confirmed(request, target)
