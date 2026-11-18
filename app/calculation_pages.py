"""Pages of the calculation (issues #161, #186).

One list of all calculations (or of one article), like the productions. New, edit, copy, view
and delete open in the pop-up; without JavaScript they are pages of their own. Calculating is
open to everybody who may look; saving, editing, copying, marking and deleting need the right to
edit. A calculation lives under its own number, its article follows from it.
"""

import re
from collections.abc import Callable
from datetime import date
from urllib.parse import quote

import httpx2
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import (
    calc_form,
    calc_store,
    calculation_files,
    printer_models,
    production_form,
    spool_list,
    stock,
    variants,
)
from app.article_pages import confirmed, varied, wants_popup
from app.calculation_file_store import FileStore
from app.models import AppSettings, Article, Calculation, Device, PrinterModel
from app.permissions import Permission, has_permission, requires
from app.two_factor import Cipher

MAX_ID = 2**31 - 1
_ID = re.compile(r"[0-9]{1,10}")
LIST = "/calculation"
FILE_SUFFIX = ".gcode.3mf"


def number(text: str) -> int:
    """An id from the address or a form; anything else (or out of range) is "not found"."""
    if not _ID.fullmatch(text) or not 0 < int(text) <= MAX_ID:
        raise HTTPException(status_code=404)
    return int(text)


def download_name(number: str, day: date) -> str:
    """The name of the downloaded file: article number and date, safe for every system."""
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", number).strip("._") or "calculation"
    return f"{clean}-{day.isoformat()}{FILE_SUFFIX}"


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
    file_store: FileStore,
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
        file_hint = False
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
            if draft.file_model and draft.printer.isdigit() and int(draft.printer) in tables:
                device = db.get(Device, int(draft.printer))
                model = (
                    db.get(PrinterModel, device.printer_model_id)
                    if device.printer_model_id is not None
                    else None
                )
                file_hint = not calculation_files.printer_matches(
                    draft.file_model, device.name, model.name if model else None
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
            file_hint=file_hint,
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
                # The copy gets its own file
                draft.file_token = (
                    file_store.copy_of(source.file_stored) if can_edit(request) else ""
                ) or ""
                if not draft.file_token:
                    draft.file_name = ""
        return page(request, draft, article_id, f"{LIST}/new", filter_text)[0]

    @app.get(f"{LIST}/new", response_class=HTMLResponse, dependencies=view_right)
    async def new_calculation(request: Request) -> HTMLResponse:
        return await run_in_threadpool(open_page, request)

    # --- Start and the way "with file" (issue #181) -------------------------------------------

    def article_from(text: str) -> int | None:
        """The article of the address (checked, 404 when there is none), or None."""
        text = text.strip()
        if not text:
            return None
        with new_session() as db:
            return article_of(db, number(text)).id

    @app.get(f"{LIST}/start", response_class=HTMLResponse, dependencies=view_right)
    def start_calculation(request: Request) -> HTMLResponse:
        article_id = article_from(request.query_params.get("article", ""))
        return varied(
            render(
                request,
                "calculation_start.html",
                "nav.calculation",
                popup=wants_popup(request),
                article_id=article_id,
            )
        )

    def file_page(
        request: Request,
        article_id: int | None,
        printer: str = "",
        errors: dict[str, str] | None = None,
    ) -> HTMLResponse:
        with new_session() as db:
            printers = production_form.load_printers(db).choices
        return varied(
            render(
                request,
                "calculation_file.html",
                "nav.calculation",
                status_code=422 if errors else 200,
                popup=wants_popup(request),
                article_id=article_id,
                printers=printers,
                printer=printer,
                errors=errors or {},
            )
        )

    @app.get(f"{LIST}/file", response_class=HTMLResponse, dependencies=view_right)
    def file_calculation(request: Request) -> HTMLResponse:
        return file_page(request, article_from(request.query_params.get("article", "")))

    def plates_page(
        request: Request,
        article_id: int | None,
        printer: str,
        payload: str,
        plates: list[calculation_files.Plate],
        info: dict[str, str],
        picked: list[int],
        error: str | None = None,
    ) -> HTMLResponse:
        return varied(
            render(
                request,
                "calculation_plates.html",
                "nav.calculation",
                status_code=422 if error else 200,
                popup=wants_popup(request),
                article_id=article_id,
                printer=printer,
                payload=payload,
                plates=calculation_files.views_of(plates, language),
                picked=picked,
                error=error,
                file_name=info["file_name"],
                file_token=info["file_token"],
                file_printer=info["file_printer"],
                file_model=info["file_model"],
            )
        )

    async def read_upload(request: Request) -> Response:
        form = await request.form()
        article_text = form.get("article")
        article_id = await run_in_threadpool(
            article_from, article_text if isinstance(article_text, str) else ""
        )
        printer_text = form.get("printer")
        printer = printer_text.strip() if isinstance(printer_text, str) else ""
        errors: dict[str, str] = {}
        with new_session() as db:
            known = {str(item) for item, _ in production_form.load_printers(db).choices}
        if printer not in known:
            errors["printer"] = "calc.error.printer"
        upload = form.get("file")
        found = None
        name = ""
        if not isinstance(upload, UploadFile) or not upload.filename:
            errors["file"] = "calc.file.error.none"
        else:
            name = calculation_files.clean_name(upload.filename)
            data = await upload.read(calculation_files.MAX_UPLOAD + 1)
            found, key = await run_in_threadpool(calculation_files.read_upload, data, name)
            if key:
                errors["file"] = key
        if errors or found is None:
            return file_page(request, article_id, printer, errors)
        payload = calculation_files.payload_of(found)
        plates = calculation_files.plates_of(payload)
        token = await run_in_threadpool(file_store.keep, data)
        info = {
            "file_token": token,
            "file_name": name,
            "file_printer": (found.printer_name or "")[: calculation_files.MAX_NAME],
            "file_model": (found.printer_model or "")[: calculation_files.MAX_NAME],
        }
        picked = [plates[0].index] if len(plates) == 1 else []
        return plates_page(request, article_id, printer, payload, plates, info, picked)

    @app.post(f"{LIST}/file", response_class=HTMLResponse, dependencies=view_right)
    async def upload_calculation_file(request: Request) -> Response:
        return await read_upload(request)

    @app.post(f"{LIST}/file/take", response_class=HTMLResponse, dependencies=view_right)
    async def take_plates(request: Request) -> Response:
        form = await request.form()

        def text(name: str, limit: int) -> str:
            value = form.get(name)
            return value.strip()[:limit] if isinstance(value, str) else ""

        article_id = await run_in_threadpool(article_from, text("article", 12))
        printer = text("printer", 12)
        payload = form.get("plates")
        info = {
            "file_token": text("file_token", 32),
            "file_name": text("file_name", calculation_files.MAX_NAME),
            "file_printer": text("file_printer", calculation_files.MAX_NAME),
            "file_model": text("file_model", calculation_files.MAX_NAME),
        }
        try:
            plates = calculation_files.plates_of(payload if isinstance(payload, str) else "")
        except ValueError:
            return file_page(request, article_id, printer, {"file": "calc.file.error.invalid"})
        numbers = [value for value in form.getlist("plate") if isinstance(value, str)]
        picked = calculation_files.chosen(plates, numbers)
        if picked is None:
            shown = [int(item) for item in numbers if item.isdigit()]
            return plates_page(
                request, article_id, printer, payload, plates, info, shown, "calc.file.error.plate"
            )
        minutes, rows, numbers_text = calculation_files.merged(picked, language)
        draft = calc_form.Draft(
            printer=printer,
            print_minutes=minutes,
            rows=rows,
            file_name=info["file_name"],
            file_token=info["file_token"],
            file_plates=numbers_text,
            file_printer=info["file_printer"],
            file_model=info["file_model"],
        )
        filter_text = str(article_id) if article_id is not None else ""
        return await run_in_threadpool(
            lambda: page(request, draft, article_id, f"{LIST}/new", filter_text)[0]
        )

    def gone_page(
        request: Request,
        draft: calc_form.Draft,
        article_id: int,
        form_action: str,
        filter_text: str,
        editing: bool,
    ) -> Response:
        """The waiting upload is not there any more: the form again, with a notice."""
        return page(
            request,
            draft,
            article_id,
            form_action,
            filter_text,
            422,
            {"file": "calc.file.error.gone"},
            save_failed=True,
            editing=editing,
        )[0]

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
        wants_file = item_id is None and bool(draft.file_token)
        if wants_file and not file_store.has_pending(draft.file_token):
            return gone_page(request, draft, article_id, form_action, filter_text, editing)
        stored_name: str | None = None
        if wants_file:
            stored_name = file_store.adopt(draft.file_token)
            if stored_name is None:
                return gone_page(request, draft, article_id, form_action, filter_text, editing)
        try:
            with new_session() as db, db.begin():
                article = article_of(db, article_id)
                if item_id is None:
                    file = (
                        (draft.file_name or f"calculation{FILE_SUFFIX}", stored_name)
                        if stored_name
                        else None
                    )
                    calc_store.create(
                        db, request.state.user.id, article, outcome.plan, language, file
                    )
                else:
                    item = stored_of(db, item_id)
                    calc_store.update(
                        db, request.state.user.id, article, item, outcome.plan, language
                    )
                target = back_to_list(article, filter_text, "saved")
        except BaseException:
            # Nothing was saved, so the file must not stay behind
            file_store.delete(stored_name)
            raise
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
                "file_name": item.file_name if file_store.path_of(item.file_stored) else None,
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

    @app.get(LIST + "/{calculation_id}/file", dependencies=view_right)
    def download_calculation_file(calculation_id: int) -> Response:
        with new_session() as db:
            item = stored_of(db, calculation_id)
            article = article_of(db, item.article_id)
            path = file_store.path_of(item.file_stored)
            name = download_name(article.number, item.created_on)
        if path is None:
            raise HTTPException(status_code=404)
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=name,
            headers={"X-Content-Type-Options": "nosniff"},
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
