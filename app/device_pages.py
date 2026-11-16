"""The settings tab "Geräte" (issue #157): printers and dryers with their hourly rates.

Admin only. Devices are never deleted, only switched off; a switched off device does not
count for the drying surcharge. Rates and residual value are calculated for the page.
"""

from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, auth, device_form, devices
from app.article_pages import saved_in_popup, wants_popup
from app.models import Device
from app.permissions import Permission, requires
from app.settings_form import format_decimal

LIST_ADDRESS = "/settings/devices"
CENTS = Decimal("0.01")
RATE_PLACES = Decimal("0.0001")


def add_device_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
    language: str,
) -> None:
    manage = [requires(Permission.MANAGE)]

    def number(value: Decimal, places: Decimal = CENTS) -> str:
        return format_decimal(value.quantize(places), language)

    def day(value: date) -> str:
        return value.strftime("%d.%m.%Y") if language == "de" else value.isoformat()

    def device_view(device: Device, today: date) -> dict:
        view = {
            "id": device.id,
            "kind": device.kind,
            "name": device.name,
            "active": device.active,
            "bought_on": day(device.bought_on),
            "price": number(device.price),
            "years": number(device.years),
            "maintenance_pct": number(device.maintenance_pct),
            "residual": number(
                devices.residual_value(device.price, device.years, device.bought_on, today)
            ),
        }
        if device.kind == "printer":
            view["hours_per_year"] = number(device.hours_per_year)
            view["rate"] = number(
                devices.printer_rate(
                    device.price, device.years, device.hours_per_year, device.maintenance_pct
                ),
                RATE_PLACES,
            )
        else:
            view["yearly"] = number(
                devices.dryer_yearly_cost(device.price, device.years, device.maintenance_pct)
            )
        return view

    def list_data() -> dict:
        today = date.today()
        with new_session() as db:
            rows = list(db.scalars(select(Device).order_by(Device.name, Device.id)))
        active = [row for row in rows if row.active]
        surcharge = devices.drying_surcharge(
            [
                devices.dryer_yearly_cost(row.price, row.years, row.maintenance_pct)
                for row in active
                if row.kind == "dryer"
            ],
            [row.hours_per_year for row in active if row.kind == "printer"],
        )
        views = [device_view(row, today) for row in rows]
        return {
            "printers": [view for view in views if view["kind"] == "printer"],
            "dryers": [view for view in views if view["kind"] == "dryer"],
            "surcharge": number(surcharge, RATE_PLACES),
        }

    @app.get(LIST_ADDRESS, response_class=HTMLResponse, dependencies=manage)
    def devices_page(request: Request) -> HTMLResponse:
        return render(request, "devices.html", "nav.settings", **list_data())

    def checked_kind(kind: str | None) -> str:
        if kind not in device_form.KINDS:
            raise HTTPException(status_code=404)
        return kind

    def form_page(
        request: Request, kind: str, device_id: int | None, values: dict, errors: dict, status=200
    ) -> HTMLResponse:
        response = render(
            request,
            "device_form.html",
            "nav.settings",
            status_code=status,
            popup=wants_popup(request),
            kind=kind,
            device_id=device_id,
            values=values,
            errors=errors,
        )
        # The same address gives a different answer for HTMX, so caches must tell them apart.
        response.headers["Vary"] = "HX-Request"
        return response

    def form_values(device: Device) -> dict[str, str]:
        values = {
            "name": device.name,
            "bought_on": device.bought_on.isoformat(),
            "price": format_decimal(device.price, language),
            "years": format_decimal(device.years, language),
            "maintenance_pct": format_decimal(device.maintenance_pct, language),
        }
        if device.hours_per_year is not None:
            values["hours_per_year"] = format_decimal(device.hours_per_year, language)
        return values

    @app.get(f"{LIST_ADDRESS}/new", response_class=HTMLResponse, dependencies=manage)
    def new_device_page(request: Request, kind: str | None = None) -> HTMLResponse:
        return form_page(request, checked_kind(kind), None, {}, {})

    def loaded(db: Session, device_id: int) -> Device:
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(status_code=404)
        return device

    @app.get(f"{LIST_ADDRESS}/{{device_id}}/edit", response_class=HTMLResponse, dependencies=manage)
    def edit_device_page(request: Request, device_id: int) -> HTMLResponse:
        with new_session() as db:
            device = loaded(db, device_id)
            kind, values = device.kind, form_values(device)
        return form_page(request, kind, device_id, values, {})

    def done(request: Request) -> Response:
        if wants_popup(request):
            return saved_in_popup()
        return RedirectResponse(LIST_ADDRESS, status_code=303)

    def log_values(values: dict[str, object]) -> dict[str, object]:
        return {name: value for name, value in values.items() if value is not None}

    def create(actor_id: int, kind: str, values: dict[str, object]) -> None:
        with new_session() as db, db.begin():
            db.add(Device(kind=kind, active=True, created_at=auth.utcnow(), **values))
            details = [
                {"field": name, "old": "", "new": str(value)}
                for name, value in log_values(values).items()
            ]
            audit.record_change(db, actor_id, "device.created", str(values["name"]), details)

    def change(actor_id: int, device_id: int, values: dict[str, object]) -> None:
        with new_session() as db, db.begin():
            device = loaded(db, device_id)
            old = {name: getattr(device, name) for name in values}
            changed = audit.field_changes(
                {k: v for k, v in old.items() if v is not None},
                log_values(values),
            )
            for name, value in values.items():
                setattr(device, name, value)
            # Saving without any change leaves no entry in the change log.
            if changed:
                audit.record_change(db, actor_id, "device.updated", device.name, changed)

    @app.post(f"{LIST_ADDRESS}/new", response_class=HTMLResponse, dependencies=manage)
    async def create_device(request: Request) -> Response:
        raw = await read_form(request, ("kind", *device_form.FIELD_NAMES))
        kind = checked_kind(raw.get("kind"))
        values, errors = device_form.validate(kind, raw)
        if errors:
            return form_page(request, kind, None, raw, errors, status=422)
        await run_in_threadpool(create, request.state.user.id, kind, values)
        return done(request)

    @app.post(
        f"{LIST_ADDRESS}/{{device_id}}/edit", response_class=HTMLResponse, dependencies=manage
    )
    async def edit_device(request: Request, device_id: int) -> Response:
        with new_session() as db:
            kind = loaded(db, device_id).kind
        raw = await read_form(request, device_form.FIELD_NAMES)
        values, errors = device_form.validate(kind, raw)
        if errors:
            return form_page(request, kind, device_id, raw, errors, status=422)
        await run_in_threadpool(change, request.state.user.id, device_id, values)
        return done(request)

    def set_active(actor_id: int, device_id: int, active: bool) -> None:
        with new_session() as db, db.begin():
            device = loaded(db, device_id)
            if device.active == active:
                return
            device.active = active
            audit.record_change(
                db, actor_id, "device.activated" if active else "device.deactivated", device.name
            )

    @app.post(f"{LIST_ADDRESS}/{{device_id}}/active", dependencies=manage)
    async def switch_device(request: Request, device_id: int) -> Response:
        raw = await read_form(request, ("active",))
        await run_in_threadpool(
            set_active, request.state.user.id, device_id, raw.get("active") == "1"
        )
        return RedirectResponse(LIST_ADDRESS, status_code=303)
