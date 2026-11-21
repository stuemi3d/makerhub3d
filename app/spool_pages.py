"""The page "Spulen zuordnen" (issue #236): Bambuddy spools without a filament, a proposal for
each, confirm one row or all rows.

Bambuddy is only read. What is stored is the spool number, the filament and, as values, the
price per kg and the nominal weight of the spool at the moment of the assignment. Admin and
employee may assign; everything the form sends is checked against Bambuddy's list and the
database (an invented number is ignored), and the values come from Bambuddy, not from the form.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

import httpx2
from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, auth, filaments, spool_assignment, spool_list
from app.bambuddy import Spool
from app.models import Filament, SpoolAssignment
from app.permissions import Permission, requires
from app.settings_form import format_decimal
from app.two_factor import Cipher

PAGE_ADDRESS = "/filaments/spools"
ASSIGN_ADDRESS = "/filaments/spools/assign"
NEW = "new"
NOT_SET_UP = {"calc.spools.not_set_up": "filaments.spools.not_set_up"}


def filament_name(item: Filament) -> str:
    return filaments.display_name(item.brand, item.material, item.subtype, item.color_name)


def waiting_spools(
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    transport: httpx2.BaseTransport | None,
) -> tuple[list[Spool], str | None]:
    """The spools of Bambuddy (also archived ones) that have no filament yet, by number, and the
    translation key of the problem if Bambuddy cannot be read (then the list is empty)."""
    found = spool_list.load(new_session, cipher, transport, include_archived=True)
    if found.problem:
        return [], NOT_SET_UP.get(found.problem, found.problem)
    with new_session() as db:
        assigned = set(db.scalars(select(SpoolAssignment.spool_id)))
    return sorted((s for s in found.spools if s.id not in assigned), key=lambda s: s.id), None


def add_spool_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
    language: str,
) -> None:
    edit = [requires(Permission.EDIT)]

    def waiting() -> tuple[list[Spool], str | None]:
        transport = getattr(app.state, "bambuddy_transport", None)
        return waiting_spools(new_session, cipher, transport)

    def number(value: Decimal | None, places: str = "0.01") -> str | None:
        if value is None:
            return None
        return format_decimal(value.quantize(Decimal(places)), language)

    def page_data(
        spools: list[Spool], choices: dict[str, str], texts: dict[str, str]
    ) -> list[dict]:
        with new_session() as db:
            items = list(db.scalars(select(Filament)))
            learned: dict[str, int] = {}
            for key, filament_id in db.execute(
                select(SpoolAssignment.spool_key, SpoolAssignment.filament_id).order_by(
                    SpoolAssignment.id
                )
            ):
                learned[key] = filament_id  # the newest assignment wins
        candidates = [
            spool_assignment.Candidate(
                item.id, item.brand, item.material, item.subtype, item.color_name
            )
            for item in items
        ]
        options = sorted(
            ((item.id, filament_name(item)) for item in items), key=lambda o: o[1].casefold()
        )
        rows = []
        first_new: dict[str, int] = {}  # spool key -> the first spool that proposes it as new
        for spool in spools:
            proposed = spool_assignment.propose(spool, candidates, learned)
            default = f"f{proposed.filament_id}" if proposed.filament_id else NEW
            like = None
            if default == NEW:
                like = first_new.setdefault(spool_assignment.spool_key(spool), spool.id)
                like = None if like == spool.id else like
            rows.append(
                {
                    "id": spool.id,
                    "brand": spool.brand or "",
                    "material": spool.material,
                    "subtype": spool.subtype or "",
                    "color_name": spool.color_name or "",
                    "color": spool.color or "",
                    "price": number(spool.cost_per_kg),
                    "weight": number(spool.label_weight, "1"),
                    "old": spool.archived,
                    "like": like,
                    "other_brand": proposed.other_brand,
                    "choice": choices.get(f"choice_{spool.id}", default),
                    "typed_name": texts.get(f"color_name_{spool.id}", ""),
                    "options": options,
                }
            )
        return rows

    def show(
        request: Request,
        choices: dict[str, str],
        errors: dict[int, str],
        status_code: int = 200,
    ) -> HTMLResponse:
        spools, problem = waiting()
        rows = [] if problem else page_data(spools, choices, choices)
        return render(
            request,
            "filament_spools.html",
            "nav.filaments",
            status_code=status_code,
            rows=rows,
            problem=problem,
            errors=errors,
        )

    @app.get(PAGE_ADDRESS, response_class=HTMLResponse, dependencies=edit)
    def spools_page(request: Request) -> HTMLResponse:
        return show(request, {}, {})

    def field_names(spools: list[Spool]) -> tuple[str, ...]:
        names = ["scope"]
        for spool in spools:
            names += [f"choice_{spool.id}", f"color_name_{spool.id}"]
        return tuple(names)

    def assign(actor_id: int, spools: list[Spool], raw: dict[str, str]) -> dict[int, str]:
        """Assigns the chosen spools; returns errors by spool number (then nothing is saved)."""
        scope = raw.get("scope", "")
        chosen = [s for s in spools if scope == "all" or scope == str(s.id)]
        todo = [(s, raw.get(f"choice_{s.id}", "")) for s in chosen]
        todo = [(s, choice) for s, choice in todo if choice]
        errors: dict[int, str] = {}
        forms: dict[int, dict[str, object]] = {}
        for spool, choice in todo:
            if choice == NEW:
                values, problems = filaments.validate(
                    spool_assignment.new_filament_form(spool, raw.get(f"color_name_{spool.id}"))
                )
                if problems:
                    errors[spool.id] = next(iter(problems.values()))
                else:
                    forms[spool.id] = values
        if errors:
            return errors
        for spool, choice in todo:
            try:
                with new_session() as db, db.begin():
                    if db.scalar(
                        select(SpoolAssignment.id).where(SpoolAssignment.spool_id == spool.id)
                    ):
                        continue
                    if choice == NEW:
                        item = get_or_create(db, actor_id, forms[spool.id])
                    elif choice[:1] == "f" and choice[1:].isdigit():
                        item = db.get(Filament, int(choice[1:]))
                        if item is None:
                            continue
                    else:
                        continue
                    db.add(
                        SpoolAssignment(
                            spool_id=spool.id,
                            filament_id=item.id,
                            price_per_kg=spool.cost_per_kg,
                            weight_g=spool.label_weight,
                            spool_key=spool_assignment.spool_key(spool),
                            assigned_at=auth.utcnow(),
                        )
                    )
                    db.flush()
                    audit.record_change(
                        db,
                        actor_id,
                        "filament.spool_assigned",
                        filament_name(item),
                        [{"field": "spool", "old": "", "new": spool_assignment.spool_text(spool)}],
                    )
            except IntegrityError:
                continue  # the same spool was assigned at the same moment
        return {}

    def get_or_create(db: Session, actor_id: int, values: dict[str, object]) -> Filament:
        key = filaments.identity_key(
            str(values["brand"]),
            str(values["material"]),
            str(values["subtype"]),
            str(values["color_name"]),
        )
        item = db.scalar(select(Filament).where(Filament.identity_key == key))
        if item is None:
            item = Filament(**values, identity_key=key, created_at=auth.utcnow())
            db.add(item)
            db.flush()
            audit.record_change(db, actor_id, "filament.created", filament_name(item))
        return item

    @app.post(ASSIGN_ADDRESS, response_class=HTMLResponse, dependencies=edit)
    async def assign_spools(request: Request) -> Response:
        spools, problem = await run_in_threadpool(waiting)
        if problem:
            return RedirectResponse(PAGE_ADDRESS, status_code=303)
        raw = await read_form(request, field_names(spools))
        errors = await run_in_threadpool(assign, request.state.user.id, spools, raw)
        if errors:
            return show(request, raw, errors, 422)
        return RedirectResponse(PAGE_ADDRESS, status_code=303)
