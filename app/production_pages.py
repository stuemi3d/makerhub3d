"""Page Production (issue #119): the prints of Bambuddy, ignoring and bringing them back."""

import re
from collections.abc import Awaitable, Callable
from datetime import date

import httpx2
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import print_list, production, production_form, production_view, stock
from app.article_pages import wants_popup
from app.models import AppSettings, Article
from app.permissions import Permission, requires
from app.two_factor import Cipher

_PRINT_ID = re.compile(r"[1-9][0-9]{0,8}")
# Ids beyond this cannot exist (and would overflow the database): answered with 404
MAX_ID = 2**31 - 1


def add_production_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
    language: str,
) -> None:
    view_right = [requires(Permission.VIEW)]
    edit_right = [requires(Permission.EDIT)]

    def read_prints(timeout: float | None = None) -> print_list.PrintList:
        transport: httpx2.BaseTransport | None = getattr(app.state, "bambuddy_transport", None)
        return print_list.load(new_session, cipher, transport, language, timeout)

    app.state.read_prints = read_prints

    def production_page(
        request: Request,
        view: str,
        sort: str | None,
        direction: str | None,
        status_code: int = 200,
        error: str | None = None,
    ) -> HTMLResponse:
        view = view if view in print_list.VIEWS else "open"
        prints = read_prints()
        rows, links = print_list.arranged(prints.rows, view, "/production", sort, direction)
        return render(
            request,
            "production.html",
            "nav.production",
            status_code=status_code,
            prints=prints,
            rows=rows,
            view=view,
            views=print_list.VIEWS,
            sort_links=links,
            error=error,
        )

    @app.get("/production", response_class=HTMLResponse, dependencies=view_right)
    async def production_list(
        request: Request,
        view: str = "open",
        sort: str | None = None,
        direction: str | None = Query(None, alias="dir"),
    ) -> HTMLResponse:
        return await run_in_threadpool(production_page, request, view, sort, direction)

    def change(request: Request, action: str, raw: dict[str, str]) -> Response:
        view = raw.get("view", "open")
        archive_text = raw.get("archive_id", "").strip()
        if not _PRINT_ID.fullmatch(archive_text):
            return production_page(request, view, None, None, 422, "production.error.print_id")
        archive_id = int(archive_text)
        error = None
        with stock.booking_lock, new_session() as db, db.begin():
            if action == "ignore":
                error = production.ignore(db, request.state.user.id, archive_id)
            else:
                production.unignore(db, request.state.user.id, archive_id)
        if error:
            return production_page(request, view, None, None, 422, error)
        target = view if view in print_list.VIEWS else "open"
        return RedirectResponse(f"/production?view={target}", status_code=303)

    @app.post("/production/ignore", response_class=HTMLResponse, dependencies=edit_right)
    async def ignore_print(request: Request) -> Response:
        raw = await read_form(request, ("archive_id", "view"))
        return await run_in_threadpool(change, request, "ignore", raw)

    @app.post("/production/unignore", response_class=HTMLResponse, dependencies=edit_right)
    async def unignore_print(request: Request) -> Response:
        raw = await read_form(request, ("archive_id", "view"))
        return await run_in_threadpool(change, request, "unignore", raw)

    # --- Creating a production (issues #127, #140) --------------------------------------------

    def form_page(
        request: Request,
        draft: production_form.Draft,
        prints: print_list.PrintList,
        status_code: int = 200,
        errors: dict[str, str] | None = None,
        preview: dict[str, str] | None = None,
    ) -> HTMLResponse:
        errors = errors or {}
        found = {row.archive_id: row for row in prints.rows}
        shown = []
        for archive_id in draft.ids:
            row = found.get(archive_id)
            if row is None:
                continue
            sent = draft.costs.get(archive_id, {})

            def value(part: str, shown_value, sent=sent) -> str:
                if sent.get(part) is not None:
                    return sent[part]
                return production_form.input_text(shown_value, language)

            shown.append(
                {
                    "row": row,
                    "material": value("material", row.material_value),
                    "electricity": value("electricity", row.electricity_value),
                    "wear": value("wear", row.wear_value),
                    "missing": None in (row.material_value, row.electricity_value, row.wear_value),
                }
            )
        with new_session() as db:
            articles = [
                {"id": a.id, "label": f"{a.number} · {a.name}"}
                for a in db.scalars(
                    select(Article).where(Article.active.is_(True)).order_by(Article.number_key)
                )
            ]
            settings = db.get(AppSettings, 1)
            default_rate = settings.labour_rate if settings else None
        labor_rate = (
            draft.labor_rate
            if draft.labor_rate is not None
            else production_form.input_text(default_rate, language)
        )
        return render(
            request,
            "production_new.html",
            "nav.production",
            status_code=status_code,
            popup=wants_popup(request),
            prints=prints,
            draft=draft,
            shown_prints=shown,
            any_missing=any(item["missing"] for item in shown),
            articles=articles,
            errors=errors,
            preview=preview,
            labor_minutes=draft.labor_minutes or "",
            labor_rate=labor_rate,
            today=date.today().isoformat(),
        )

    def open_form(
        request: Request, draft: production_form.Draft
    ) -> tuple[HTMLResponse | None, production_form.Checked, print_list.PrintList]:
        prints = read_prints()
        if prints.not_set_up or prints.error:
            checked = production_form.Checked()
            code = "production.not_set_up" if prints.not_set_up else prints.error[0]
            checked.errors["prints"] = code
            return form_page(request, draft, prints, 422, checked.errors), checked, prints
        found = {row.archive_id: row for row in prints.rows}
        with new_session() as db:
            checked = production_form.check(db, draft, found, date.today())
        return None, checked, prints

    @app.get("/production/new", response_class=HTMLResponse, dependencies=edit_right)
    async def new_production(request: Request) -> HTMLResponse:
        ids = [str(v) for v in request.query_params.getlist("prints")]
        draft = production_form.read(None, ids)
        draft.date = date.today().isoformat()

        def work() -> HTMLResponse:
            prints = read_prints()
            if prints.not_set_up or prints.error:
                code = "production.not_set_up" if prints.not_set_up else prints.error[0]
                return form_page(request, draft, prints, 422, {"prints": code})
            found = {row.archive_id: row for row in prints.rows}
            problem = _prints_problem(draft, found)
            if problem:
                return form_page(request, draft, prints, 422, {"prints": problem})
            return form_page(request, draft, prints)

        return await run_in_threadpool(work)

    def _prints_problem(draft: production_form.Draft, found: dict) -> str | None:
        if not draft.ids:
            return "production.error.no_print"
        for archive_id in draft.ids:
            row = found.get(archive_id)
            if row is None or row.status not in production_form.SELECTABLE_STATUSES:
                return "production.error.print_unusable"
            if row.state != "open":
                return "production.error.print_taken"
        return None

    def handle_form(request: Request, draft: production_form.Draft) -> Response:
        failed_page, checked, prints = open_form(request, draft)
        if failed_page is not None:
            return failed_page
        if checked.errors:
            return form_page(request, draft, prints, 422, checked.errors)
        if draft.action == "preview":
            with new_session() as db:
                errors, total, line = production.preview(
                    db, checked.prints, checked.result, checked.labor
                )
            if errors:
                return form_page(request, draft, prints, 422, errors)
            printed, labor_cost, _ = production.costs(checked.prints, checked.labor)
            shown = {
                "print": production_form.money_text(printed, language),
                "labor": production_form.money_text(labor_cost, language),
                "total": production_form.money_text(total, language),
                "cost": production_form.money_text(line.cost, language),
                "per_piece": production_form.money_text(line.cost / line.quantity, language),
            }
            return form_page(request, draft, prints, preview=shown)
        with stock.booking_lock, new_session() as db, db.begin():
            production_id, errors = production.book(
                db,
                request.state.user.id,
                checked.booked_on,
                draft.note,
                checked.prints,
                checked.result,
                checked.labor,
            )
        if production_id is None:
            return form_page(request, draft, prints, 422, errors)
        target = "/production/list?booked=1"
        if wants_popup(request):
            # The page behind the pop-up is the list of prints: go to the productions
            return Response(status_code=204, headers={"HX-Redirect": target})
        return RedirectResponse(target, status_code=303)

    @app.post("/production/new", response_class=HTMLResponse, dependencies=edit_right)
    async def create_production(request: Request) -> Response:
        draft = production_form.read(await request.form())
        return await run_in_threadpool(handle_form, request, draft)

    # --- Productions: list, detail, cancelling ------------------------------------------------

    @app.get("/production/list", response_class=HTMLResponse, dependencies=view_right)
    def production_overview(
        request: Request,
        booked: str | None = None,
        cancelled: str | None = None,
        sort: str | None = None,
        direction: str | None = Query(None, alias="dir"),
    ) -> HTMLResponse:
        with new_session() as db:
            rows = production_view.list_rows(db, language)
        rows, links = production_view.arranged(rows, sort, direction)
        return render(
            request,
            "production_list.html",
            "nav.production",
            rows=rows,
            sort_links=links,
            booked=booked == "1",
            cancelled=cancelled == "1",
        )

    @app.get("/production/{production_id}", response_class=HTMLResponse, dependencies=view_right)
    def production_detail(request: Request, production_id: int) -> HTMLResponse:
        if not 0 < production_id <= MAX_ID:
            raise HTTPException(status_code=404)
        with new_session() as db:
            item = production_view.detail(db, production_id, language)
        if item is None:
            raise HTTPException(status_code=404)
        return render(request, "production_detail.html", "nav.production", item=item)

    def cancel_page(
        request: Request, production_id: int, status_code: int = 200, error: str | None = None
    ) -> HTMLResponse:
        if not 0 < production_id <= MAX_ID:
            raise HTTPException(status_code=404)
        with new_session() as db:
            item = production_view.detail(db, production_id, language)
        if item is None:
            raise HTTPException(status_code=404)
        return render(
            request,
            "production_cancel.html",
            "nav.production",
            status_code=status_code,
            item=item,
            error=error,
        )

    @app.get(
        "/production/{production_id}/cancel", response_class=HTMLResponse, dependencies=edit_right
    )
    def cancel_question(request: Request, production_id: int) -> HTMLResponse:
        return cancel_page(request, production_id)

    def do_cancel(request: Request, production_id: int) -> Response:
        if not 0 < production_id <= MAX_ID:
            raise HTTPException(status_code=404)
        try:
            with stock.booking_lock, new_session() as db, db.begin():
                error = production.cancel(db, request.state.user.id, production_id)
        except LookupError:
            raise HTTPException(status_code=404) from None
        if error:
            return cancel_page(request, production_id, 422, error)
        return RedirectResponse("/production/list?cancelled=1", status_code=303)

    @app.post(
        "/production/{production_id}/cancel", response_class=HTMLResponse, dependencies=edit_right
    )
    async def cancel_production(request: Request, production_id: int) -> Response:
        return await run_in_threadpool(do_cancel, request, production_id)
