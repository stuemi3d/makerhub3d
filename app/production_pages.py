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

from app import (
    list_filter,
    print_list,
    production,
    production_form,
    production_view,
    stock,
    table_sort,
)
from app.archive_parts import archive_parts
from app.article_pages import confirmed, varied, wants_popup
from app.bambuddy import BambuddyClient, BambuddyError
from app.calc_form import spool_name
from app.default_price import standard_price_spools
from app.i18n import translate
from app.list_filter import Field
from app.models import AppSettings, Article, BambuddyConnection
from app.permissions import Permission, requires
from app.production_parts import detect, same_article, stored_detected
from app.two_factor import Cipher

_PRINT_ID = re.compile(r"[1-9][0-9]{0,8}")
# Ids beyond this cannot exist (and would overflow the database): answered with 404
MAX_ID = 2**31 - 1
# Fields of the forms "ignore" and "bring back": the print, then the way back to the list
BACK_FIELDS = ("archive_id", "view", "sort", "dir", *print_list.BACK_FILTER)


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

    def parts_client() -> BambuddyClient | None:
        """The client for the parts of the plates; None when Bambuddy is not set up."""
        transport: httpx2.BaseTransport | None = getattr(app.state, "bambuddy_transport", None)
        with new_session() as db:
            row = db.get(BambuddyConnection, 1)
            url, token = row.url, row.api_key_encrypted
        api_key = cipher.decrypt(token) if cipher is not None and token else None
        if not url or api_key is None:
            return None
        return BambuddyClient(url, api_key, transport=transport)

    def default_price_notes(rows: list[print_list.PrintRow]) -> dict[int, str]:
        """Per print: the notice that Bambuddy charged its default price (issue #228). Empty
        when the usage cannot be read; the notice is only help and never stops anything."""
        client = parts_client()
        if client is None or not rows:
            return {}
        try:
            usages = client.usage()
            spools = {spool.id: spool for spool in client.spools(include_archived=True)}
            default_price = client.default_filament_cost()
        except BambuddyError:
            return {}
        notes: dict[int, str] = {}
        for row in rows:
            found = standard_price_spools(
                row.finished_at, row.printer_id, usages, spools, default_price
            )
            if found:
                names = ", ".join(spool_name(spool) for spool in found)
                notes[row.archive_id] = translate(language, "production.default_price").format(
                    spools=names
                )
        return notes

    def detect_parts(draft: production_form.Draft) -> None:
        """Fills the parts mask from what Bambuddy and the file say (a first visit). Whatever
        goes wrong, the mask stays empty and the user picks the mode and types by hand."""
        client = parts_client()
        if client is None:
            return
        found: dict[int, list] = {}
        modes: list[str] = []
        for archive_id in draft.ids:
            try:
                lines, mode = detect(archive_parts(client, archive_id))
            except BambuddyError:
                continue
            if lines:
                found[archive_id] = lines
                modes.append(mode)
                draft.detected[archive_id] = stored_detected(lines)
        if not modes:
            return
        draft.parts_mode = "different" if "different" in modes else "same"
        for archive_id in draft.ids:
            lines = found.get(archive_id, [])
            if draft.parts_mode == "same":
                lines = same_article(lines)
            raws = [production_form.raw_line(x, language) for x in lines]
            draft.parts[archive_id] = raws or [production_form.RawLine()]
        production_form.refresh_auto(draft, force=True)

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
        everything = print_list.in_view(prints.rows, view)
        fields = print_list.filter_fields(everything, view, article_choices(everything))
        values = list_filter.read(fields, dict(request.query_params))
        kept = [row for row in prints.rows if print_list.keeps(row, values)]
        active = list_filter.params(fields, values)
        rows, links = print_list.arranged(kept, view, "/production", sort, direction, active)
        chosen = table_sort.chosen_sort(print_list.SORT_KEYS, sort, direction)
        sorting = {"sort": chosen[0], "dir": chosen[1]} if chosen else {}
        bar = list_filter.build(
            "/production",
            fields,
            values,
            lambda key: translate(language, key),
            {"view": view, **sorting},
            lambda day: stock.format_date(day, language),
        )
        return render(
            request,
            "production.html",
            "nav.production",
            status_code=status_code,
            prints=prints,
            rows=rows,
            has_any=bool(everything),
            view=view,
            views=print_list.VIEWS,
            sort_links=links,
            filters=bar,
            # Filter and sorting go along with ignoring, so the list comes back as it was
            back={**active, **sorting},
            error=error,
        )

    def article_choices(rows: list[print_list.PrintRow]) -> list[tuple[int, str]]:
        """The result articles of the given prints as (id, "number · name"), by number."""
        wanted = {i for row in rows for i in row.article_ids}
        if not wanted:
            return []
        with new_session() as db:
            found = db.execute(
                select(Article.id, Article.number, Article.name)
                .where(Article.id.in_(wanted))
                .order_by(Article.number_key)
            ).all()
        return [(a.id, f"{a.number} · {a.name}") for a in found]

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
        return RedirectResponse(print_list.way_back(view, raw), status_code=303)

    @app.post("/production/ignore", response_class=HTMLResponse, dependencies=edit_right)
    async def ignore_print(request: Request) -> Response:
        raw = await read_form(request, BACK_FIELDS)
        return await run_in_threadpool(change, request, "ignore", raw)

    @app.post("/production/unignore", response_class=HTMLResponse, dependencies=edit_right)
    async def unignore_print(request: Request) -> Response:
        raw = await read_form(request, BACK_FIELDS)
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
        with new_session() as db:
            printers = production_form.load_printers(db)
        if not printers.rates and "prints" not in errors:
            errors = {**errors, "prints": "production.error.no_printer"}
        found = {row.archive_id: row for row in prints.rows}
        shown = []
        notes = default_price_notes([found[i] for i in draft.ids if i in found])
        for archive_id in draft.ids:
            row = found.get(archive_id)
            if row is None:
                continue
            sent = draft.costs.get(archive_id, {})
            device = production_form.chosen_device(draft, row, printers)
            machine = production_form.machine_default(row.seconds, printers.rates.get(device))
            hint = production_form.machine_hint(row, device)

            def value(part: str, shown_value, sent=sent) -> str:
                if sent.get(part) is not None:
                    return sent[part]
                return production_form.input_text(shown_value, language)

            shown.append(
                {
                    "row": row,
                    "material": value("material", row.material_value),
                    "electricity": value("electricity", row.electricity_value),
                    "machine": value("machine", machine),
                    "device": device,
                    "missing": None in (row.material_value, row.electricity_value),
                    "default_price": notes.get(archive_id),
                    "machine_hint": None if machine is not None else hint,
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
            printers=printers.choices,
            articles=articles,
            parts_modes=production_form.PARTS_MODES,
            parts=production_form.parts_view(draft, language, errors),
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
            detect_parts(draft)
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
        if draft.action in ("device", "parts"):
            # Another printer or a change of the parts: draw the form again, without checking
            # the rest
            return form_page(request, draft, prints)
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
                checked.failed_pieces,
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
        draft = production_form.read(await request.form(), language=language)
        return await run_in_threadpool(handle_form, request, draft)

    # --- Productions: list, detail, cancelling ------------------------------------------------

    def list_fields(rows: list[production_view.ListRow]) -> tuple[Field, ...]:
        """The filter bar: the choices are the articles and printers that productions have."""
        with new_session() as db:
            articles = db.execute(
                select(Article.id, Article.number, Article.name)
                .where(Article.id.in_({i for row in rows for i in row.article_ids}))
                .order_by(Article.number_key)
            ).all()
        printers = sorted({name for row in rows for name in row.printers}, key=str.casefold)
        return (
            Field(
                "article",
                "production.filter.article",
                (
                    ("", "production.filter.article.all"),
                    *((str(a.id), f"{a.number} · {a.name}") for a in articles),
                ),
            ),
            Field("from", "production.filter.from", kind="date"),
            Field("to", "production.filter.to", kind="date"),
            Field(
                "printer",
                "production.filter.printer",
                (("", "production.filter.printer.all"), *((n, n) for n in printers)),
            ),
            Field(
                "status",
                "production.filter.state",
                (
                    ("all", "production.filter.status.all"),
                    ("booked", "production.filter.status.booked"),
                    ("cancelled", "production.filter.status.cancelled"),
                ),
                "all",
            ),
        )

    def keeps(row: production_view.ListRow, values: dict[str, str]) -> bool:
        first, last = list_filter.date_of(values["from"]), list_filter.date_of(values["to"])
        return (
            (first is None or row.booked_on >= first)
            and (last is None or row.booked_on <= last)
            and (not values["article"] or int(values["article"]) in row.article_ids)
            and (not values["printer"] or values["printer"] in row.printers)
            and (values["status"] == "all" or row.cancelled == (values["status"] == "cancelled"))
        )

    @app.get("/production/list", response_class=HTMLResponse, dependencies=view_right)
    def production_overview(
        request: Request,
        booked: str | None = None,
        cancelled: str | None = None,
        sort: str | None = None,
        direction: str | None = Query(None, alias="dir"),
    ) -> HTMLResponse:
        with new_session() as db:
            everything = production_view.list_rows(db, language)
        fields = list_fields(everything)
        values = list_filter.read(fields, dict(request.query_params))
        rows = [row for row in everything if keeps(row, values)]
        chosen = table_sort.chosen_sort(production_view.SORT_KEYS, sort, direction)
        sorting = {"sort": chosen[0], "dir": chosen[1]} if chosen else {}
        rows, links = production_view.arranged(
            rows, sort, direction, list_filter.params(fields, values)
        )
        bar = list_filter.build(
            "/production/list",
            fields,
            values,
            lambda key: translate(language, key),
            sorting,
            lambda day: stock.format_date(day, language),
        )
        return render(
            request,
            "production_list.html",
            "nav.production",
            rows=rows,
            has_any=bool(everything),
            sort_links=links,
            filters=bar,
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
        return varied(
            render(
                request,
                "production_cancel.html",
                "nav.production",
                status_code=status_code,
                popup=wants_popup(request),
                item=item,
                error=error,
            )
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
        # The cancel question comes from the list or from the production itself; the list shows
        # the notice, so a pop-up leads there too.
        return confirmed(request, "/production/list?cancelled=1", leave=True)

    @app.post(
        "/production/{production_id}/cancel", response_class=HTMLResponse, dependencies=edit_right
    )
    async def cancel_production(request: Request, production_id: int) -> Response:
        return await run_in_threadpool(do_cancel, request, production_id)
