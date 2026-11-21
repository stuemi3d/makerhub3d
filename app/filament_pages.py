"""The page "Filamente" (issue #235): create, edit, switch off and delete filaments.

Everybody signed in may read; admin and employee may change (like the articles). Nothing hangs
on a filament yet, so deleting is possible; once spools are assigned (a later task), only
switching off is left. Every change goes into the change log.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, auth, filament_stock, filaments, spool_list, spool_pages
from app.article_pages import confirmed, saved_in_popup, varied, wants_popup
from app.models import Filament, SpoolAssignment
from app.permissions import Permission, has_permission, requires
from app.settings_form import format_decimal
from app.spool_manage_pages import changed_price
from app.table_sort import chosen_sort, sort_links
from app.two_factor import Cipher

LIST_ADDRESS = "/filaments"
CENTS = Decimal("0.01")
GRAMS = Decimal("0.001")


def add_filament_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
    language: str,
) -> None:
    view = [requires(Permission.VIEW)]
    edit = [requires(Permission.EDIT)]

    def name_of(item: Filament) -> str:
        return filaments.display_name(item.brand, item.material, item.subtype, item.color_name)

    def shown(value: Decimal | None, places: Decimal) -> str | None:
        """Decimal comma in German; a minimum like 2 stays "2" and 2.5 stays "2,5"."""
        if value is None:
            return None
        text = format_decimal(value.quantize(places), language)
        sep = "," if language == "de" else "."
        return text.rstrip("0").rstrip(sep) if sep in text and places == GRAMS else text

    def row_view(item: Filament) -> dict:
        return {
            "id": item.id,
            "name": name_of(item),
            "brand": item.brand,
            "material": item.material,
            "color": item.color,
            "price": shown(item.standard_price, CENTS),
            "price_value": item.standard_price,
            "min_stock": shown(item.min_stock_kg, GRAMS),
            "active": item.active,
        }

    def loaded(db: Session, filament_id: int) -> Filament:
        item = db.get(Filament, filament_id)
        if item is None:
            raise HTTPException(status_code=404)
        return item

    def sorted_rows(rows: list[dict], key: str, direction: str) -> list[dict]:
        """Rows without a price stay at the end, whichever the direction."""
        reverse = direction == "desc"
        if key == "price":
            filled = [r for r in rows if r["price_value"] is not None]
            empty = [r for r in rows if r["price_value"] is None]
            return sorted(filled, key=lambda r: r["price_value"], reverse=reverse) + empty
        column = {
            "name": lambda r: r["name"].casefold(),
            "brand": lambda r: (r["brand"].casefold(), r["name"].casefold()),
            "material": lambda r: (r["material"].casefold(), r["name"].casefold()),
            "status": lambda r: (r["active"], r["name"].casefold()),
        }
        return sorted(rows, key=column[key], reverse=reverse)

    def waiting_count(request: Request, found: spool_list.SpoolList | None) -> int | None:
        """How many Bambuddy spools wait for a filament; None when it cannot be told (only
        people who may assign are asked, and a Bambuddy problem is no reason for an error)."""
        if found is None or not has_permission(request.state.user.role, Permission.EDIT):
            return None
        spools, problem = spool_pages.waiting_in(new_session, found)
        return None if problem else len(spools)

    def held_by_filament(db: Session) -> dict[int, list[filament_stock.Held]]:
        held: dict[int, list[filament_stock.Held]] = {}
        for row in db.scalars(select(SpoolAssignment)):
            held.setdefault(row.filament_id, []).append(
                filament_stock.Held(row.spool_id, row.price_per_kg, row.weight_g, row.assigned_at)
            )
        return held

    @app.get(LIST_ADDRESS, response_class=HTMLResponse, dependencies=view)
    def filaments_page(
        request: Request, sort: str | None = None, dir: str | None = None
    ) -> HTMLResponse:
        chosen = chosen_sort(filaments.SORT_KEYS, sort, dir)
        key, direction = chosen or ("name", "asc")
        with new_session() as db:
            items = list(db.scalars(select(Filament)))
            held = held_by_filament(db)
        # Bambuddy is asked once for the whole page (and not at all when nothing is assigned
        # and nobody may assign). It is read afresh every time, so that a price changed back in
        # Bambuddy shows at once.
        found = None
        if held or has_permission(request.state.user.role, Permission.EDIT):
            transport = getattr(app.state, "bambuddy_transport", None)
            found = spool_list.load(new_session, cipher, transport, include_archived=True)
        live = {s.id: s for s in found.spools} if found and not found.problem else None
        unreadable = bool(held) and live is None and found is not None and bool(found.problem)
        rows = []
        price_changes = 0
        for item in items:
            mine = held.get(item.id, [])
            changed = (
                sum(
                    1
                    for h in mine
                    if changed_price(h.price_per_kg, live.get(h.spool_id)) is not None
                )
                if live is not None
                else 0
            )
            price_changes += changed
            # Nothing assigned: the stock is 0 without asking Bambuddy
            summary = filament_stock.summarize(
                mine, live if live is not None or mine else {}, item.min_stock_kg
            )
            row = stock_view(row_view(item), summary, item.active)
            row["price_changes"] = changed
            rows.append(row)
        return render(
            request,
            "filaments.html",
            "nav.filaments",
            waiting=waiting_count(request, found),
            stock_unreadable=unreadable,
            price_changes=price_changes,
            filaments=sorted_rows(rows, key, direction),
            sort_links=sort_links(LIST_ADDRESS, filaments.SORT_KEYS, chosen, ("name", "asc")),
        )

    def stock_view(row: dict, summary: filament_stock.Summary, active: bool) -> dict:
        row["stock"] = shown(summary.stock_kg, CENTS)
        row["spools"] = summary.spool_count
        row["average"] = shown(summary.average_price, CENTS)
        row["last"] = shown(summary.last_price, CENTS)
        row["low"] = summary.low and active
        return row

    def form_page(
        request: Request,
        filament_id: int | None,
        values: dict,
        errors: dict,
        status_code: int = 200,
    ) -> HTMLResponse:
        """The form: the whole page, or only the form for the pop-up (same template)."""
        return varied(
            render(
                request,
                "filament_form.html",
                "nav.filaments",
                status_code=status_code,
                filament_id=filament_id,
                values=values,
                errors=errors,
                popup=wants_popup(request),
            )
        )

    @app.get(f"{LIST_ADDRESS}/new", response_class=HTMLResponse, dependencies=edit)
    def new_filament_page(request: Request) -> HTMLResponse:
        return form_page(request, None, {}, {})

    def form_values(item: Filament) -> dict[str, str]:
        return {
            "brand": item.brand,
            "material": item.material,
            "subtype": item.subtype,
            "color_name": item.color_name,
            "color": item.color,
            "standard_price": shown(item.standard_price, CENTS) or "",
            "min_stock_kg": shown(item.min_stock_kg, GRAMS) or "",
            "note": item.note,
        }

    @app.get(f"{LIST_ADDRESS}/{{filament_id}}/edit", response_class=HTMLResponse, dependencies=edit)
    def edit_filament_page(request: Request, filament_id: int) -> HTMLResponse:
        with new_session() as db:
            values = form_values(loaded(db, filament_id))
        return form_page(request, filament_id, values, {})

    def taken(db: Session, key: str, own_id: int | None) -> bool:
        found = db.scalar(select(Filament.id).where(Filament.identity_key == key))
        return found is not None and found != own_id

    def save(actor_id: int, filament_id: int | None, raw: dict[str, str]) -> dict[str, str]:
        """Creates (filament_id None) or changes a filament; the errors, empty when saved."""
        values, errors = filaments.validate(raw)
        if errors:
            return errors
        key = filaments.identity_key(
            str(values["brand"]),
            str(values["material"]),
            str(values["subtype"]),
            str(values["color_name"]),
        )
        try:
            with new_session() as db, db.begin():
                if taken(db, key, filament_id):
                    return {"material": "filament.error.taken"}
                if filament_id is None:
                    item = Filament(**values, identity_key=key, created_at=auth.utcnow())
                    db.add(item)
                    db.flush()
                    audit.record_change(db, actor_id, "filament.created", name_of(item))
                    return {}
                item = loaded(db, filament_id)
                old = {name: getattr(item, name) for name in values}
                for name, value in values.items():
                    setattr(item, name, value)
                item.identity_key = key
                changes = audit.field_changes(
                    {k: "" if v is None else v for k, v in old.items()},
                    {k: "" if v is None else v for k, v in values.items()},
                )
                if changes:
                    audit.record_change(db, actor_id, "filament.changed", name_of(item), changes)
        except IntegrityError:
            # Two saves at the same moment: the other one was faster.
            return {"material": "filament.error.taken"}
        return {}

    async def submit(request: Request, filament_id: int | None) -> Response:
        raw = await read_form(request, filaments.FIELD_NAMES)
        errors = await run_in_threadpool(save, request.state.user.id, filament_id, raw)
        if errors:
            return form_page(request, filament_id, raw, errors, 422)
        if wants_popup(request):
            return saved_in_popup()
        return RedirectResponse(LIST_ADDRESS, status_code=303)

    @app.post(f"{LIST_ADDRESS}/new", response_class=HTMLResponse, dependencies=edit)
    async def create_filament(request: Request) -> Response:
        return await submit(request, None)

    @app.post(f"{LIST_ADDRESS}/{{filament_id}}", response_class=HTMLResponse, dependencies=edit)
    async def change_filament(request: Request, filament_id: int) -> Response:
        with new_session() as db:
            loaded(db, filament_id)
        return await submit(request, filament_id)

    def set_active(actor_id: int, filament_id: int, active: bool) -> None:
        with new_session() as db, db.begin():
            item = loaded(db, filament_id)
            if item.active != active:
                item.active = active
                action = "filament.activated" if active else "filament.deactivated"
                audit.record_change(db, actor_id, action, name_of(item))

    @app.post(f"{LIST_ADDRESS}/{{filament_id}}/active", dependencies=edit)
    async def switch_filament(request: Request, filament_id: int) -> Response:
        raw = await read_form(request, ("active",))
        await run_in_threadpool(
            set_active, request.state.user.id, filament_id, raw.get("active") == "1"
        )
        return RedirectResponse(LIST_ADDRESS, status_code=303)

    def has_spools(db: Session, filament_id: int) -> bool:
        found = db.scalar(
            select(SpoolAssignment.id).where(SpoolAssignment.filament_id == filament_id).limit(1)
        )
        return found is not None

    @app.get(
        f"{LIST_ADDRESS}/{{filament_id}}/delete", response_class=HTMLResponse, dependencies=edit
    )
    def delete_filament_page(request: Request, filament_id: int) -> HTMLResponse:
        with new_session() as db:
            name = name_of(loaded(db, filament_id))
            blocked = has_spools(db, filament_id)
        return varied(
            render(
                request,
                "filament_delete.html",
                "nav.filaments",
                popup=wants_popup(request),
                filament_id=filament_id,
                filament_name=name,
                blocked=blocked,
            )
        )

    def remove(actor_id: int, filament_id: int) -> None:
        with new_session() as db, db.begin():
            item = loaded(db, filament_id)
            # Checked here, not only by hiding the button: with spools only switching off is left.
            if has_spools(db, filament_id):
                raise HTTPException(status_code=409)
            name = name_of(item)
            db.delete(item)
            audit.record_change(db, actor_id, "filament.deleted", name)

    @app.post(f"{LIST_ADDRESS}/{{filament_id}}/delete", dependencies=edit)
    async def delete_filament(request: Request, filament_id: int) -> Response:
        await run_in_threadpool(remove, request.state.user.id, filament_id)
        return confirmed(request, LIST_ADDRESS)
