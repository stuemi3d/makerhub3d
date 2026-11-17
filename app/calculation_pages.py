"""Page "New calculation" (issue #161, part a): calculate by hand, live, without saving."""

import re
from collections.abc import Callable

import httpx2
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import calc_form, printer_models, production_form, spool_list, variants
from app.models import AppSettings, Article, Device
from app.permissions import Permission, requires
from app.two_factor import Cipher

MAX_ID = 2**31 - 1


def add_calculation_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    render: Callable[..., HTMLResponse],
    language: str,
) -> None:
    view_right = [requires(Permission.VIEW)]

    def page(request: Request, draft: calc_form.Draft, article_text: str) -> HTMLResponse:
        article = None
        target = None
        if article_text:
            if not (re.fullmatch(r"[0-9]{1,10}", article_text) and 0 < int(article_text) <= MAX_ID):
                raise HTTPException(status_code=404)
            with new_session() as db:
                found = db.get(Article, int(article_text))
                if found is None:
                    raise HTTPException(status_code=404)
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
        )
        outcome = calc_form.evaluate(draft, ctx)
        return render(
            request,
            "calculation_new.html",
            "nav.calculation",
            status_code=200,
            draft=draft,
            article=article,
            printers=printers.choices,
            outcome=outcome,
            errors=outcome.errors,
            can_add_row=len(draft.rows) < calc_form.MAX_ROWS,
        )

    @app.get("/calculation/new", response_class=HTMLResponse, dependencies=view_right)
    async def new_calculation(request: Request) -> HTMLResponse:
        article = request.query_params.get("article", "").strip()
        return await run_in_threadpool(page, request, calc_form.Draft(), article)

    @app.post("/calculation/new", response_class=HTMLResponse, dependencies=view_right)
    async def calculate(request: Request) -> HTMLResponse:
        form = await request.form()
        article = form.get("article")
        draft = calc_form.read(form)
        return await run_in_threadpool(
            page, request, draft, article.strip() if isinstance(article, str) else ""
        )
