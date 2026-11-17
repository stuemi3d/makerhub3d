"""Printer models on the settings tab "Geräte" (issue #174): create, power table, delete.

Admin only. A model is created only by choosing one that Bambuddy reports; its power table
(material -> kWh per hour) is edited in a pop-up. A model can only be deleted while no printer
uses it. Every change goes into the change log.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

import httpx2
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, auth, device_import, printer_model_form
from app.article_pages import confirmed, saved_in_popup, varied, wants_popup
from app.models import Device, PrinterModel, PrinterModelPower
from app.permissions import Permission, requires
from app.settings_form import format_decimal
from app.two_factor import Cipher

LIST_ADDRESS = "/settings/devices"
MODELS_ADDRESS = f"{LIST_ADDRESS}/models"
# One empty row below the stored ones; "Material hinzufügen" adds more (issue #177)
EXTRA_ROWS = 1


def ordered(model_id: int):
    """The rows of a model in the order the person chose."""
    return (
        select(PrinterModelPower)
        .where(PrinterModelPower.model_id == model_id)
        .order_by(PrinterModelPower.position, PrinterModelPower.id)
    )


def printers_of(db: Session, model_id: int) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(Device).where(Device.printer_model_id == model_id)
        )
        or 0
    )


def model_views(db: Session, language: str) -> list[dict]:
    """The models for the table on the devices page: name, power rows, number of printers."""
    views = []
    for model in db.scalars(select(PrinterModel).order_by(PrinterModel.name, PrinterModel.id)):
        powers = db.scalars(ordered(model.id))
        count = printers_of(db, model.id)
        views.append(
            {
                "id": model.id,
                "name": model.name,
                "powers": [
                    {"material": p.material, "kwh": format_decimal(p.kwh_per_hour, language)}
                    for p in powers
                ],
                "printers": count,
                "deletable": count == 0,
            }
        )
    return views


def add_printer_model_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
    language: str,
) -> None:
    manage = [requires(Permission.MANAGE)]

    def bambuddy_models() -> device_import.ModelChoice:
        transport: httpx2.BaseTransport | None = getattr(app.state, "bambuddy_transport", None)
        return device_import.load_models(new_session, cipher, transport)

    def loaded(db: Session, model_id: int) -> PrinterModel:
        model = db.get(PrinterModel, model_id)
        if model is None:
            raise HTTPException(status_code=404)
        return model

    def row_values(raw: dict[str, str]) -> list[dict[str, str]]:
        last = max(
            (
                index
                for index in range(printer_model_form.MAX_ROWS + 1)
                if raw.get(f"material_{index}", "").strip() or raw.get(f"kwh_{index}", "").strip()
            ),
            default=-1,
        )
        count = min(printer_model_form.MAX_ROWS + 1, last + 1 + EXTRA_ROWS)
        return [
            {"material": raw.get(f"material_{i}", ""), "kwh": raw.get(f"kwh_{i}", "")}
            for i in range(count)
        ]

    def stored_values(db: Session, model_id: int) -> dict[str, str]:
        powers = db.scalars(ordered(model_id))
        raw: dict[str, str] = {}
        for index, power in enumerate(powers):
            raw[f"material_{index}"] = power.material
            raw[f"kwh_{index}"] = format_decimal(power.kwh_per_hour, language)
        return raw

    def form_page(
        request: Request,
        model_id: int | None,
        name: str,
        raw: dict[str, str],
        errors: dict[str, str],
        status: int = 200,
        choice: device_import.ModelChoice | None = None,
    ) -> HTMLResponse:
        response = render(
            request,
            "printer_model_form.html",
            "nav.settings",
            status_code=status,
            popup=wants_popup(request),
            model_id=model_id,
            name=name,
            rows=row_values(raw),
            errors=errors,
            choice=choice,
        )
        return varied(response)

    @app.get(f"{MODELS_ADDRESS}/new", response_class=HTMLResponse, dependencies=manage)
    async def new_model_page(request: Request) -> HTMLResponse:
        # Bambuddy is only read; this runs off the event loop.
        choice = await run_in_threadpool(bambuddy_models)
        return form_page(request, None, "", {}, {}, choice=choice)

    def write_rows(db: Session, model_id: int, values: list[tuple[str, Decimal]]) -> list[dict]:
        """Replaces the rows of the model; returns the changes for the log."""
        old_rows = list(db.scalars(ordered(model_id)))
        old = {p.material: p for p in old_rows}
        details: list[dict[str, str]] = []
        kept: set[str] = set()
        for position, (material, kwh) in enumerate(values):
            kept.add(material)
            row = old.get(material)
            if row is None:
                db.add(
                    PrinterModelPower(
                        model_id=model_id, material=material, kwh_per_hour=kwh, position=position
                    )
                )
                details.append({"field": material, "old": "", "new": str(kwh)})
                continue
            row.position = position
            if row.kwh_per_hour != kwh:
                details.append({"field": material, "old": str(row.kwh_per_hour), "new": str(kwh)})
                row.kwh_per_hour = kwh
        # A change of the order alone counts too: compare the rows that stay.
        before = [row.material for row in old_rows if row.material in kept]
        after = [material for material, _ in values if material in old]
        if before != after:
            details.append({"field": "order", "old": ", ".join(before), "new": ", ".join(after)})
        for material, row in old.items():
            if material not in kept:
                details.append({"field": material, "old": str(row.kwh_per_hour), "new": ""})
                db.delete(row)
        return details

    def create(actor_id: int, name: str, values: list[tuple[str, Decimal]]) -> None:
        with new_session() as db, db.begin():
            model = PrinterModel(name=name, created_at=auth.utcnow())
            db.add(model)
            db.flush()
            details = write_rows(db, model.id, values)
            audit.record_change(db, actor_id, "printer_model.created", name, details)

    @app.post(f"{MODELS_ADDRESS}/new", response_class=HTMLResponse, dependencies=manage)
    async def create_model(request: Request) -> Response:
        raw = await read_form(request, ("name", *printer_model_form.field_names()))
        choice = await run_in_threadpool(bambuddy_models)
        # Only a model that Bambuddy reports now and that is not created yet is accepted.
        name = choice.find(raw.get("name", ""))
        if name is None:
            errors = {"name": "model.error.name"}
            return form_page(request, None, "", raw, errors, status=422, choice=choice)
        values, errors = printer_model_form.validate_rows(raw)
        if errors:
            return form_page(request, None, name, raw, errors, status=422, choice=choice)
        try:
            await run_in_threadpool(create, request.state.user.id, name, values)
        except IntegrityError:
            # Two admins created the same model at the same moment.
            errors = {"name": "model.error.name"}
            return form_page(request, None, "", raw, errors, status=422, choice=choice)
        if wants_popup(request):
            return saved_in_popup()
        return Response(status_code=303, headers={"Location": LIST_ADDRESS})

    @app.get(
        f"{MODELS_ADDRESS}/{{model_id}}/edit", response_class=HTMLResponse, dependencies=manage
    )
    def edit_model_page(request: Request, model_id: int) -> HTMLResponse:
        with new_session() as db:
            name = loaded(db, model_id).name
            raw = stored_values(db, model_id)
        return form_page(request, model_id, name, raw, {})

    def change(actor_id: int, model_id: int, values: list[tuple[str, Decimal]]) -> None:
        with new_session() as db, db.begin():
            model = loaded(db, model_id)
            details = write_rows(db, model_id, values)
            # Saving without any change leaves no entry in the change log.
            if details:
                audit.record_change(db, actor_id, "printer_model.updated", model.name, details)

    @app.post(
        f"{MODELS_ADDRESS}/{{model_id}}/edit", response_class=HTMLResponse, dependencies=manage
    )
    async def edit_model(request: Request, model_id: int) -> Response:
        with new_session() as db:
            name = loaded(db, model_id).name
        raw = await read_form(request, printer_model_form.field_names())
        values, errors = printer_model_form.validate_rows(raw)
        if errors:
            return form_page(request, model_id, name, raw, errors, status=422)
        await run_in_threadpool(change, request.state.user.id, model_id, values)
        if wants_popup(request):
            return saved_in_popup()
        return Response(status_code=303, headers={"Location": LIST_ADDRESS})

    @app.get(
        f"{MODELS_ADDRESS}/{{model_id}}/delete", response_class=HTMLResponse, dependencies=manage
    )
    def delete_model_page(request: Request, model_id: int) -> HTMLResponse:
        with new_session() as db:
            name = loaded(db, model_id).name
            count = printers_of(db, model_id)
        return varied(
            render(
                request,
                "printer_model_delete.html",
                "nav.settings",
                popup=wants_popup(request),
                model_id=model_id,
                model_name=name,
                printer_count=count,
            )
        )

    def remove(actor_id: int, model_id: int) -> None:
        with new_session() as db, db.begin():
            model = loaded(db, model_id)
            # Checked here, not only by hiding the button: printers must keep their table.
            if printers_of(db, model_id):
                raise HTTPException(status_code=409)
            for row in db.scalars(
                select(PrinterModelPower).where(PrinterModelPower.model_id == model_id)
            ):
                db.delete(row)
            # The rows go first, so the foreign key is never violated.
            db.flush()
            db.delete(model)
            audit.record_change(db, actor_id, "printer_model.deleted", model.name)

    @app.post(f"{MODELS_ADDRESS}/{{model_id}}/delete", dependencies=manage)
    async def delete_model(request: Request, model_id: int) -> Response:
        await read_form(request, ())
        await run_in_threadpool(remove, request.state.user.id, model_id)
        return confirmed(request, LIST_ADDRESS)
