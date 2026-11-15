"""Page Production (issue #119): the prints of Bambuddy, ignoring and bringing them back."""

import re
from collections.abc import Awaitable, Callable

import httpx2
from fastapi import FastAPI, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import print_list, production, stock
from app.permissions import Permission, requires
from app.two_factor import Cipher

_PRINT_ID = re.compile(r"[1-9][0-9]{0,8}")


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
