"""Pages "New calculation" and the saved calculations of an article (issue #161).

Part a: calculate by hand with a live result. Part b: save it at an article or a variant, look
at it, copy, edit, make it the main one and delete it. A calculation is only reachable through
its own article.
"""

import re
from collections.abc import Callable

import httpx2
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import calc_form, calc_store, printer_models, production_form, spool_list, stock, variants
from app.article_pages import confirmed, varied, wants_popup
from app.models import AppSettings, Article, Calculation, Device
from app.permissions import Permission, has_permission, requires
from app.two_factor import Cipher

MAX_ID = 2**31 - 1
_ID = re.compile(r"[0-9]{1,10}")


def number(text: str) -> int:
    """An id from the address or a form; anything else (or out of range) is "not found"."""
    if not _ID.fullmatch(text) or not 0 < int(text) <= MAX_ID:
        raise HTTPException(status_code=404)
    return int(text)


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

    def stored_of(db: Session, article_id: int, calculation_id: int) -> Calculation:
        item = (
            calc_store.load(db, article_id, calculation_id)
            if 0 < calculation_id <= MAX_ID
            else None
        )
        if item is None:
            raise HTTPException(status_code=404)
        return item

    def can_edit(request: Request) -> bool:
        return has_permission(request.state.user.role, Permission.EDIT)

    def page(
        request: Request,
        draft: calc_form.Draft,
        article_id: int | None,
        form_action: str,
        status_code: int = 200,
        extra_errors: dict[str, str] | None = None,
        save_failed: bool = False,
        editing: int | None = None,
        saving: bool = False,
    ) -> tuple[HTMLResponse, calc_form.Outcome]:
        article = None
        target = None
        if article_id is not None:
            with new_session() as db:
                found = article_of(db, article_id)
                article = {"id": found.id, "number": found.number, "name": found.name}
                target = variants.effective(found, "selling_price")
        with new_session() as db:
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
            # Nothing can be saved: the same page, with the notice
            status_code, save_failed = 422, True
        response = render(
            request,
            "calculation_new.html",
            "nav.calculation",
            status_code=status_code,
            draft=draft,
            article=article,
            printers=printers.choices,
            outcome=outcome,
            errors=outcome.errors,
            can_add_row=len(draft.rows) < calc_form.MAX_ROWS,
            form_action=form_action,
            can_save=article is not None and can_edit(request),
            needs_article=article is None and can_edit(request),
            save_failed=save_failed,
            editing=editing,
        )
        return response, outcome

    def open_page(
        request: Request, article_text: str, copy_text: str, draft: calc_form.Draft | None = None
    ) -> HTMLResponse:
        article_id = number(article_text) if article_text else None
        if copy_text:
            if article_id is None:
                raise HTTPException(status_code=404)
            with new_session() as db:
                source = stored_of(db, article_id, number(copy_text))
                draft = calc_store.draft_of(source, language)
        return page(request, draft or calc_form.Draft(), article_id, "/calculation/new")[0]

    @app.get("/calculation/new", response_class=HTMLResponse, dependencies=view_right)
    async def new_calculation(request: Request) -> HTMLResponse:
        article = request.query_params.get("article", "").strip()
        copy = request.query_params.get("copy", "").strip()
        return await run_in_threadpool(open_page, request, article, copy)

    def work(
        request: Request,
        draft: calc_form.Draft,
        article_text: str,
        form_action: str,
        item_id: int | None = None,
    ) -> Response:
        """Calculates again, or saves when the form asks for it."""
        article_id = number(article_text) if article_text else None
        if draft.action != "save":
            return page(request, draft, article_id, form_action, editing=item_id)[0]
        if not can_edit(request):
            raise HTTPException(status_code=403)
        if article_id is None:
            response, _ = page(
                request,
                draft,
                None,
                form_action,
                422,
                {"article": "calc.error.article"},
                save_failed=True,
                editing=item_id,
            )
            return response
        response, outcome = page(
            request, draft, article_id, form_action, editing=item_id, saving=True
        )
        if outcome.plan is None:
            return response
        with new_session() as db, db.begin():
            article = article_of(db, article_id)
            if item_id is None:
                calc_store.create(db, request.state.user.id, article, outcome.plan, language)
            else:
                item = stored_of(db, article_id, item_id)
                calc_store.update(db, request.state.user.id, article, item, outcome.plan, language)
            # The address is built from the stored article, not from the request
            target = f"/articles/{int(article.id)}/calculations?saved=1"
        return RedirectResponse(target, status_code=303)

    @app.post("/calculation/new", response_class=HTMLResponse, dependencies=view_right)
    async def calculate(request: Request) -> Response:
        form = await request.form()
        article = form.get("article")
        draft = calc_form.read(form)
        text = article.strip() if isinstance(article, str) else ""
        return await run_in_threadpool(work, request, draft, text, "/calculation/new")

    # --- One saved calculation ----------------------------------------------------------------

    def base(article_id: int, calculation_id: int) -> str:
        return f"/articles/{article_id}/calculations/{calculation_id}"

    @app.get(
        "/articles/{article_id}/calculations/{calculation_id}",
        response_class=HTMLResponse,
        dependencies=view_right,
    )
    def view_calculation(request: Request, article_id: int, calculation_id: int) -> HTMLResponse:
        with new_session() as db:
            article = article_of(db, article_id)
            item = stored_of(db, article_id, calculation_id)
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
                "materials": calc_store.detail_materials(item, language),
            }
            article_view = {"id": article.id, "number": article.number, "name": article.name}
        return render(
            request,
            "calculation_view.html",
            "nav.articles",
            article=article_view,
            item=shown,
        )

    @app.get(
        "/articles/{article_id}/calculations/{calculation_id}/edit",
        response_class=HTMLResponse,
        dependencies=edit_right,
    )
    def edit_calculation_page(
        request: Request, article_id: int, calculation_id: int
    ) -> HTMLResponse:
        with new_session() as db:
            article_of(db, article_id)
            draft = calc_store.draft_of(stored_of(db, article_id, calculation_id), language)
        action = base(article_id, calculation_id) + "/edit"
        return page(request, draft, article_id, action, editing=calculation_id)[0]

    @app.post(
        "/articles/{article_id}/calculations/{calculation_id}/edit",
        response_class=HTMLResponse,
        dependencies=edit_right,
    )
    async def edit_calculation(request: Request, article_id: int, calculation_id: int) -> Response:
        with new_session() as db:
            article_of(db, article_id)
            stored_of(db, article_id, calculation_id)
        draft = calc_form.read(await request.form())
        action = base(article_id, calculation_id) + "/edit"
        return await run_in_threadpool(
            work, request, draft, str(article_id), action, calculation_id
        )

    @app.post(
        "/articles/{article_id}/calculations/{calculation_id}/main",
        dependencies=edit_right,
    )
    def main_calculation(request: Request, article_id: int, calculation_id: int) -> Response:
        with new_session() as db, db.begin():
            article = article_of(db, article_id)
            item = stored_of(db, article_id, calculation_id)
            calc_store.make_main(db, request.state.user.id, article, item, language)
            target = f"/articles/{int(article.id)}/calculations?main=1"
        return RedirectResponse(target, status_code=303)

    @app.get(
        "/articles/{article_id}/calculations/{calculation_id}/delete",
        response_class=HTMLResponse,
        dependencies=edit_right,
    )
    def delete_calculation_page(
        request: Request, article_id: int, calculation_id: int
    ) -> HTMLResponse:
        with new_session() as db:
            article = article_of(db, article_id)
            item = stored_of(db, article_id, calculation_id)
            shown = {
                "id": item.id,
                "date": stock.format_date(item.created_on, language),
                "printer": item.printer_name,
                "article": f"{article.number} · {article.name}",
                "article_id": article.id,
            }
        return varied(
            render(
                request,
                "calculation_delete.html",
                "nav.articles",
                popup=wants_popup(request),
                item=shown,
            )
        )

    @app.post(
        "/articles/{article_id}/calculations/{calculation_id}/delete",
        dependencies=edit_right,
    )
    def delete_calculation(request: Request, article_id: int, calculation_id: int) -> Response:
        with new_session() as db, db.begin():
            article = article_of(db, article_id)
            item = stored_of(db, article_id, calculation_id)
            calc_store.remove(db, request.state.user.id, article, item, language)
            target = f"/articles/{int(article.id)}/calculations?deleted=1"
        return confirmed(request, target)
