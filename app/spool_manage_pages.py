"""Follow the assignment of the spools (issue #245): the spools of one filament with their state
in Bambuddy, the hint "price changed, take over?", assigning a spool to another filament and
releasing it.

Bambuddy is only read. The state (active, archived, no longer in Bambuddy) is worked out on each
call from Bambuddy's list; nothing is marked as gone when Bambuddy cannot be read. The stored
price and weight only change when a person takes a price over. Admin and employee may change.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, filaments, spool_list
from app.article_pages import confirmed, varied, wants_popup
from app.bambuddy import Spool
from app.models import Filament, SpoolAssignment
from app.permissions import Permission, has_permission, requires
from app.settings_form import format_decimal
from app.spool_assignment import spool_text
from app.two_factor import Cipher

CENTS = Decimal("0.01")
ACTIVE, ARCHIVED, GONE, UNKNOWN = "active", "archived", "gone", "unknown"


def state_of(live: dict[int, Spool] | None, spool_id: int) -> str:
    if live is None:
        return UNKNOWN
    spool = live.get(spool_id)
    if spool is None:
        return GONE
    return ARCHIVED if spool.archived else ACTIVE


def changed_price(stored: Decimal | None, spool: Spool | None) -> Decimal | None:
    """The price Bambuddy has now when it differs from the stored one; None when there is
    nothing to take over (same price, spool gone, or no price in Bambuddy: never replaced by
    nothing)."""
    if spool is None or spool.cost_per_kg is None:
        return None
    if stored is not None and stored.quantize(CENTS) == spool.cost_per_kg.quantize(CENTS):
        return None
    return spool.cost_per_kg


def add_spool_manage_routes(
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

    def money(value: Decimal | None) -> str | None:
        return None if value is None else format_decimal(value.quantize(CENTS), language)

    def plain(value: Decimal | None) -> str:
        return "" if value is None else str(value.quantize(CENTS))

    def transport():
        return getattr(app.state, "bambuddy_transport", None)

    def live_cached() -> dict[int, Spool] | None:
        found = spool_list.load_cached(
            app.state.spool_cache, new_session, cipher, transport(), include_archived=True
        )
        return None if found.problem else {s.id: s for s in found.spools}

    def live_fresh() -> dict[int, Spool] | None:
        found = spool_list.load(new_session, cipher, transport(), include_archived=True)
        return None if found.problem else {s.id: s for s in found.spools}

    def assignment_of(db: Session, spool_id: int) -> SpoolAssignment:
        item = db.scalar(select(SpoolAssignment).where(SpoolAssignment.spool_id == spool_id))
        if item is None:
            raise HTTPException(status_code=404)
        return item

    def filament_of(db: Session, filament_id: int) -> Filament:
        item = db.get(Filament, filament_id)
        if item is None:
            raise HTTPException(status_code=404)
        return item

    def subject_of(assignment: SpoolAssignment, live: dict[int, Spool] | None) -> str:
        spool = live.get(assignment.spool_id) if live else None
        return spool_text(spool) if spool else f"#{assignment.spool_id}"

    @app.get("/filaments/{filament_id}/spools", response_class=HTMLResponse, dependencies=view)
    def assigned_page(request: Request, filament_id: int) -> HTMLResponse:
        with new_session() as db:
            item = filament_of(db, filament_id)
            name = name_of(item)
            held = list(
                db.scalars(
                    select(SpoolAssignment)
                    .where(SpoolAssignment.filament_id == filament_id)
                    .order_by(SpoolAssignment.spool_id)
                )
            )
        live = live_cached() if held else {}
        rows = []
        for assignment in held:
            spool = live.get(assignment.spool_id) if live else None
            new_price = changed_price(assignment.price_per_kg, spool)
            rows.append(
                {
                    "id": assignment.spool_id,
                    "label": spool_text(spool) if spool else f"#{assignment.spool_id}",
                    "color": (spool.color if spool else "") or "",
                    "state": state_of(live, assignment.spool_id),
                    "price": money(assignment.price_per_kg),
                    "weight": None
                    if assignment.weight_g is None
                    else format_decimal(assignment.weight_g.quantize(Decimal("1")), language),
                    "new_price": money(new_price),
                }
            )
        return render(
            request,
            "filament_assigned.html",
            "nav.filaments",
            filament_id=filament_id,
            filament_name=name,
            rows=rows,
            unreadable=live is None,
            can_edit=has_permission(request.state.user.role, Permission.EDIT),
        )

    @app.get("/filaments/price-changes", response_class=HTMLResponse, dependencies=view)
    def changes_page(request: Request) -> HTMLResponse:
        """All spools whose price in Bambuddy differs from the stored one, over all filaments."""
        with new_session() as db:
            names = {i.id: name_of(i) for i in db.scalars(select(Filament))}
            held = list(db.scalars(select(SpoolAssignment).order_by(SpoolAssignment.spool_id)))
        live = live_cached() if held else {}
        rows = []
        for assignment in held:
            spool = live.get(assignment.spool_id) if live else None
            new_price = changed_price(assignment.price_per_kg, spool)
            if new_price is None:
                continue
            rows.append(
                {
                    "id": assignment.spool_id,
                    "filament": names[assignment.filament_id],
                    "filament_id": assignment.filament_id,
                    "label": spool_text(spool),
                    "color": (spool.color if spool else "") or "",
                    "old": money(assignment.price_per_kg),
                    "new": money(new_price),
                }
            )
        rows.sort(key=lambda r: (r["filament"].casefold(), r["id"]))
        return render(
            request,
            "filament_price_changes.html",
            "nav.filaments",
            rows=rows,
            unreadable=live is None,
            can_edit=has_permission(request.state.user.role, Permission.EDIT),
        )

    # --- take over a changed price ---------------------------------------------------------

    @app.get("/filaments/spools/{spool_id}/price", response_class=HTMLResponse, dependencies=edit)
    def price_page(request: Request, spool_id: int) -> HTMLResponse:
        with new_session() as db:
            assignment = assignment_of(db, spool_id)
            filament_id, stored = assignment.filament_id, assignment.price_per_kg
        live = live_fresh()
        spool = live.get(spool_id) if live else None
        new_price = changed_price(stored, spool)
        return varied(
            render(
                request,
                "filament_price.html",
                "nav.filaments",
                popup=wants_popup(request),
                filament_id=filament_id,
                spool_id=spool_id,
                subject=spool_text(spool) if spool else f"#{spool_id}",
                old=money(stored) or "",
                new=money(new_price),
            )
        )

    def take_price(actor_id: int, spool_id: int) -> int:
        live = live_fresh()
        with new_session() as db, db.begin():
            assignment = assignment_of(db, spool_id)
            spool = live.get(spool_id) if live else None
            new_price = changed_price(assignment.price_per_kg, spool)
            if new_price is None:
                raise HTTPException(status_code=404)
            old = assignment.price_per_kg
            assignment.price_per_kg = new_price
            audit.record_change(
                db,
                actor_id,
                "filament.spool_price_taken",
                name_of(filament_of(db, assignment.filament_id)),
                [{"field": spool_text(spool), "old": plain(old), "new": plain(new_price)}],
            )
            filament_id = assignment.filament_id
        app.state.spool_cache.clear()
        return filament_id

    @app.post("/filaments/spools/{spool_id}/price", dependencies=edit)
    async def take_price_now(request: Request, spool_id: int) -> Response:
        filament_id = await run_in_threadpool(take_price, request.state.user.id, spool_id)
        return confirmed(request, f"/filaments/{filament_id}/spools")

    # --- assign to another filament ----------------------------------------------------------

    def options_for(db: Session, current: int) -> list[tuple[int, str]]:
        items = [i for i in db.scalars(select(Filament)) if i.id != current]
        return sorted(((i.id, name_of(i)) for i in items), key=lambda o: o[1].casefold())

    def move_form(
        request: Request, spool_id: int, error: str | None = None, status_code: int = 200
    ) -> HTMLResponse:
        with new_session() as db:
            assignment = assignment_of(db, spool_id)
            options = options_for(db, assignment.filament_id)
            filament_id = assignment.filament_id
        return varied(
            render(
                request,
                "filament_move.html",
                "nav.filaments",
                status_code=status_code,
                popup=wants_popup(request),
                filament_id=filament_id,
                spool_id=spool_id,
                options=options,
                error_key=error,
            )
        )

    @app.get("/filaments/spools/{spool_id}/move", response_class=HTMLResponse, dependencies=edit)
    def move_page(request: Request, spool_id: int) -> HTMLResponse:
        return move_form(request, spool_id)

    def move(actor_id: int, spool_id: int, raw: str) -> int | None:
        """Moves the spool; the filament it came from, or None when the target is not valid."""
        live = live_cached()
        with new_session() as db, db.begin():
            assignment = assignment_of(db, spool_id)
            # no filament number is longer than nine digits (and only plain digits count)
            valid = raw.isascii() and raw.isdigit() and len(raw) <= 9
            target = db.get(Filament, int(raw)) if valid else None
            if target is None or target.id == assignment.filament_id:
                return None
            source = filament_of(db, assignment.filament_id)
            assignment.filament_id = target.id
            audit.record_change(
                db,
                actor_id,
                "filament.spool_moved",
                subject_of(assignment, live),
                [{"field": "filament", "old": name_of(source), "new": name_of(target)}],
            )
            origin = source.id
        app.state.spool_cache.clear()
        return origin

    @app.post("/filaments/spools/{spool_id}/move", response_class=HTMLResponse, dependencies=edit)
    async def move_now(request: Request, spool_id: int) -> Response:
        raw = await read_form(request, ("filament",))
        origin = await run_in_threadpool(move, request.state.user.id, spool_id, raw["filament"])
        if origin is None:
            return move_form(request, spool_id, "filaments.move.error", 422)
        return confirmed(request, f"/filaments/{origin}/spools")

    # --- release ---------------------------------------------------------------------------------

    @app.get("/filaments/spools/{spool_id}/release", response_class=HTMLResponse, dependencies=edit)
    def release_page(request: Request, spool_id: int) -> HTMLResponse:
        with new_session() as db:
            filament_id = assignment_of(db, spool_id).filament_id
        return varied(
            render(
                request,
                "filament_release.html",
                "nav.filaments",
                popup=wants_popup(request),
                filament_id=filament_id,
                spool_id=spool_id,
            )
        )

    def release(actor_id: int, spool_id: int) -> int:
        live = live_cached()
        with new_session() as db, db.begin():
            assignment = assignment_of(db, spool_id)
            filament = filament_of(db, assignment.filament_id)
            subject = subject_of(assignment, live)
            origin = filament.id
            audit.record_change(
                db,
                actor_id,
                "filament.spool_released",
                subject,
                [{"field": "filament", "old": name_of(filament), "new": ""}],
            )
            db.delete(assignment)
        app.state.spool_cache.clear()
        return origin

    @app.post("/filaments/spools/{spool_id}/release", dependencies=edit)
    async def release_now(request: Request, spool_id: int) -> Response:
        origin = await run_in_threadpool(release, request.state.user.id, spool_id)
        return confirmed(request, f"/filaments/{origin}/spools")
