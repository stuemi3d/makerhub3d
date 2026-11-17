"""The form "New calculation" (issue #161, part a): reading, checking and calculating by hand.

Nothing is saved. Everything the person typed comes back into the form. Numbers are typed with
a comma or a dot. A position (material, grams, time share) needs spools from Bambuddy; when the
chosen spool does not hold enough, another one is asked for and the grams are spread in order.
Nothing is ever calculated with a guessed value: an incomplete position means no result.
"""

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from starlette.datastructures import FormData

from app import printer_models
from app.bambuddy import Spool
from app.calculation import (
    CalculationError,
    CalculationInput,
    CalculationResult,
    MaterialLine,
    PowerLine,
    calculate,
)
from app.models import AppSettings
from app.production_form import input_text, parse_decimal
from app.settings_form import format_decimal

MAX_ROWS = 20
MAX_SPOOLS = 10
MAX_TEXT = 40
MAX_NAME = 80
CENT = Decimal("0.01")
ZERO = Decimal(0)
_WHOLE = re.compile(r"[0-9]{1,9}")
# "live" is what the form sends while someone types (no button): empty fields stay quiet then.
# "calc" and "save" (the buttons) mark every missing field.
ACTIONS = ("live", "calc", "add_row", "printer", "save")
STRICT_ACTIONS = ("calc", "save")
NO_VALUE = "\u2013"


@dataclass
class Row:
    material: str = ""
    grams: str = ""
    minutes: str = ""
    spools: list[str] = field(default_factory=list)
    # From a file (issue #181): the colour and the material as the file names it
    color: str = ""
    file_material: str = ""

    def blank(self) -> bool:
        # The time share does not count: it is prefilled when there are several materials
        return not (self.material or self.grams or any(self.spools))


@dataclass
class Draft:
    printer: str = ""
    print_minutes: str = ""
    pieces: str = ""
    rows: list[Row] = field(default_factory=lambda: [Row()])
    setup_minutes: str = ""
    rework_minutes: str = ""
    # None = not sent (then the value of the settings counts)
    scrap: str | None = None
    margin: str | None = None
    all_spools: bool = False
    action: str = "live"
    # From a file (issue #181), only shown: its name, the plates taken and its printer
    file_name: str = ""
    file_plates: str = ""
    file_printer: str = ""
    file_model: str = ""

    def blank(self) -> bool:
        """Nothing was typed yet: no errors and no result."""
        texts = [self.print_minutes, self.pieces, self.setup_minutes, self.rework_minutes]
        return not any(texts) and all(row.blank() for row in self.rows)


def _text(form: FormData, name: str, limit: int = MAX_TEXT) -> str:
    value = form.get(name)
    return value.strip()[:limit] if isinstance(value, str) else ""


_COLOR = re.compile(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?")


def valid_color(text: str) -> str:
    """A colour code like #FFFF00 as it is, anything else gives "" (it is drawn from this)."""
    return text if _COLOR.fullmatch(text) else ""


def read(form: FormData | None) -> Draft:
    """The draft from a POST form; None (first visit) gives an empty one."""
    if form is None:
        return Draft()
    count = _text(form, "rows")
    rows_wanted = int(count) if _WHOLE.fullmatch(count) else 1
    rows_wanted = min(max(rows_wanted, 1), MAX_ROWS)
    action = _text(form, "action")
    draft = Draft(
        printer=_text(form, "printer"),
        print_minutes=_text(form, "print_minutes"),
        pieces=_text(form, "pieces"),
        setup_minutes=_text(form, "setup_minutes"),
        rework_minutes=_text(form, "rework_minutes"),
        scrap=_text(form, "scrap") if "scrap" in form else None,
        margin=_text(form, "margin") if "margin" in form else None,
        all_spools=_text(form, "all_spools") == "1",
        action=action if action in ACTIONS else "live",
        file_name=_text(form, "file_name", MAX_NAME),
        file_plates=_text(form, "file_plates"),
        file_printer=_text(form, "file_printer", MAX_NAME),
        file_model=_text(form, "file_model", MAX_NAME),
    )
    if draft.action == "add_row":
        rows_wanted = min(rows_wanted + 1, MAX_ROWS)
    draft.rows = []
    for index in range(rows_wanted):
        spools = []
        for slot in range(MAX_SPOOLS):
            name = f"spool_{index}_{slot}"
            if name in form:
                spools.append(_text(form, name))
        draft.rows.append(
            Row(
                material=_text(form, f"material_{index}"),
                grams=_text(form, f"grams_{index}"),
                minutes=_text(form, f"minutes_{index}"),
                spools=spools,
                color=valid_color(_text(form, f"color_{index}")),
                file_material=_text(form, f"filemat_{index}"),
            )
        )
    return draft


def cents(value: Decimal, language: str) -> str:
    """An amount rounded (half up) to the cent for showing; the comma in German."""
    text = format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")
    return text.replace(".", ",") if language == "de" else text


def remaining(spool: Spool) -> Decimal | None:
    """Grams left on the spool; None when Bambuddy does not know its weight."""
    if spool.label_weight is None:
        return None
    return max(spool.label_weight - (spool.weight_used or ZERO), ZERO)


def spool_name(spool: Spool) -> str:
    """The text that is stored with a spool: number, brand, material and colour."""
    name = " ".join(part for part in (spool.brand, spool.material, spool.color_name) if part)
    return f"#{spool.id} {name}"


def spool_label(spool: Spool, left: Decimal, language: str) -> str:
    grams = format(left.quantize(Decimal("1"), rounding=ROUND_HALF_UP), "f")
    return f"{spool_name(spool)} · {grams} g"


@dataclass
class Slot:
    """One spool select of a position."""

    name: str
    value: str
    options: list[tuple[str, str]]


@dataclass
class RowView:
    index: int
    row: Row
    materials: list[str]
    slots: list[Slot]
    # The translation key of a note below the position (incomplete), or None
    note: str | None = None
    # The translation key of a hint under the spool choice (no material, no spool), or None
    hint: str | None = None
    # The material as the table spells it, or None
    material: str | None = None
    # The time share as the field shows it
    minutes_shown: str = ""


@dataclass(frozen=True)
class PlannedSpool:
    spool_id: int
    label: str
    grams: Decimal
    price_per_kg: Decimal


@dataclass(frozen=True)
class PlannedMaterial:
    material: str
    grams: Decimal
    minutes: Decimal
    kwh_per_hour: Decimal
    spools: list[PlannedSpool]


@dataclass(frozen=True)
class Plan:
    """A complete, valid calculation with everything that is stored when saving it."""

    printer_id: int
    printer_name: str
    print_minutes: Decimal
    pieces: int
    setup_minutes: Decimal
    rework_minutes: Decimal
    scrap_percent: Decimal
    margin_percent: Decimal
    power_price: Decimal
    machine_rate: Decimal
    labour_rate: Decimal
    rounding: str
    target_price: Decimal | None
    materials: list[PlannedMaterial]
    result: CalculationResult


@dataclass
class Outcome:
    errors: dict[str, str] = field(default_factory=dict)
    rows: list[RowView] = field(default_factory=list)
    # The translation key of why nothing can be calculated (printer without model or table)
    printer_hint: str | None = None
    # The translation key of a problem with Bambuddy
    spool_problem: str | None = None
    result: dict[str, str] | None = None
    # The same result as numbers, with what is needed to save it
    plan: Plan | None = None
    # Several materials: the time share fields are shown, with the sum and the print time
    multi: bool = False
    minutes_sum: tuple[str, str] | None = None
    # The switch "all spools" is only offered while the filter setting is on
    show_switch: bool = False


@dataclass(frozen=True)
class Context:
    """What the check needs besides the draft."""

    settings: AppSettings
    # Active printers: device id -> machine rate
    rates: dict[int, Decimal]
    # Device id -> (has a model, power table in its order)
    tables: dict[int, tuple[bool, dict[str, Decimal] | None]]
    spools: list[Spool]
    spool_problem: str | None
    # The target price of the article, when there is one
    target_price: Decimal | None
    language: str
    # Device id -> name of the printer
    names: dict[int, str] = field(default_factory=dict)


def _positive(text: str) -> Decimal | None:
    number = parse_decimal(text)
    return number if number is not None and number > 0 else None


def filter_active(ctx: "Context", draft: Draft) -> bool:
    """Only business spools are offered: the setting is on and the switch "all spools" is not."""
    return (
        ctx.settings.spool_business_only
        and not draft.all_spools
        and bool(ctx.settings.spool_category.strip())
    )


def _is_option(spool: Spool, material: str, category: str | None) -> bool:
    """`category` is the word of the filter, or None when every spool may be offered."""
    if printer_models.same_material(spool.material) != printer_models.same_material(material):
        return False
    left = remaining(spool)
    if left is None or left <= 0:
        return False
    if category is None:
        return True
    return printer_models.same_material(spool.category or "") == printer_models.same_material(
        category
    )


def _options(ctx: Context, material: str, draft: Draft) -> list[tuple[str, str]]:
    found = []
    category = ctx.settings.spool_category if filter_active(ctx, draft) else None
    for spool in ctx.spools:
        if _is_option(spool, material, category):
            left = remaining(spool) or ZERO
            found.append((str(spool.id), spool_label(spool, left, ctx.language)))
    return found


def _allocate(
    errors: dict[str, str],
    index: int,
    row: Row,
    grams: Decimal | None,
    options: list[tuple[str, str]],
    ctx: Context,
) -> tuple[list[tuple[Spool, Decimal]], int, bool]:
    """The spool lines of a position, the last spool field that counts (-1 = none) and whether
    the position is complete. Errors are written for the spool fields."""
    by_id = {str(spool.id): spool for spool in ctx.spools}
    allowed = {value for value, _ in options}
    chosen: list[tuple[int, str]] = []
    seen: set[str] = set()
    for slot, value in enumerate(row.spools):
        if not value:
            continue
        name = f"spool_{index}_{slot}"
        if value not in allowed:
            errors[name] = "calc.error.spool"
        elif value in seen:
            errors[name] = "calc.error.spool_twice"
        elif by_id[value].cost_per_kg is None or by_id[value].cost_per_kg <= 0:
            errors[name] = "calc.error.spool_price"
        else:
            seen.add(value)
            chosen.append((slot, value))
    lines: list[tuple[Spool, Decimal]] = []
    last = -1
    rest = grams
    for slot, value in chosen:
        if rest is None or rest <= 0:
            break
        spool = by_id[value]
        take = min(rest, remaining(spool) or ZERO)
        lines.append((spool, take))
        last = slot
        rest -= take
    complete = (
        grams is not None and rest is not None and rest <= 0 and not _has_error(errors, index)
    )
    return lines, last, complete


def _has_error(errors: dict[str, str], index: int) -> bool:
    return any(name.startswith(f"spool_{index}_") for name in errors)


def evaluate(draft: Draft, ctx: Context) -> Outcome:
    """Checks the draft and, when everything is complete and valid, calculates it."""
    out = Outcome(spool_problem=ctx.spool_problem)
    printer_id = int(draft.printer) if _WHOLE.fullmatch(draft.printer) else None
    table: dict[str, Decimal] = {}
    printer_ok = printer_id in ctx.rates
    if printer_ok:
        has_model, found = ctx.tables[printer_id]
        if not has_model:
            out.printer_hint = "calc.printer.no_model"
        elif not found:
            out.printer_hint = "calc.printer.no_table"
        else:
            table = found
    ready = printer_ok and bool(table)
    errors = out.errors
    strict = draft.action in STRICT_ACTIONS
    # The buttons mark what is missing even in an empty form; typing does so only after a start
    show_errors = strict or not draft.blank()
    # Something is still missing (an empty field while typing): no error mark, but no result
    missing = False

    def fail(name: str, key: str, text: str) -> None:
        """An error for the field. While typing, an empty field is only "not there yet"."""
        nonlocal missing
        if strict or text.strip():
            errors[name] = key
        else:
            missing = True

    if show_errors and not printer_ok:
        fail("printer", "calc.error.printer", draft.printer)
    scrap_text = (
        draft.scrap if draft.scrap is not None else _default(ctx.settings.scrap_rate, ctx.language)
    )
    margin_text = (
        draft.margin if draft.margin is not None else _default(ctx.settings.markup, ctx.language)
    )
    draft.scrap, draft.margin = scrap_text, margin_text

    complete = ready and show_errors
    print_minutes = _positive(draft.print_minutes)
    pieces = int(draft.pieces) if _WHOLE.fullmatch(draft.pieces) and int(draft.pieces) > 0 else None
    numbers: dict[str, Decimal | None] = {}
    if show_errors:
        if print_minutes is None:
            fail("print_minutes", "calc.error.number", draft.print_minutes)
        if pieces is None:
            fail("pieces", "calc.error.pieces", draft.pieces)
        for name, text, required in (
            ("setup_minutes", draft.setup_minutes, False),
            ("rework_minutes", draft.rework_minutes, False),
            ("scrap", scrap_text, True),
            ("margin", margin_text, True),
        ):
            number = parse_decimal(text) if text else (None if required else ZERO)
            if number is None:
                fail(name, "calc.error.number", text)
            numbers[name] = number

    materials: list[MaterialLine] = []
    power: list[PowerLine] = []
    planned: list[PlannedMaterial] = []
    filled = 0
    out.show_switch = bool(ctx.settings.spool_business_only)
    out.multi = len(draft.rows) >= 2
    # One material: its time share is the print time. Only several materials share the time.
    shared_time = sum(1 for item in draft.rows if not item.blank()) >= 2
    for index, row in enumerate(draft.rows):
        view = RowView(index, row, list(table), [])
        out.rows.append(view)
        grams = _positive(row.grams)
        material = _canonical(table, row.material)
        options = _options(ctx, material, draft) if material else []
        view.material = material
        view.minutes_shown = row.minutes or (
            draft.print_minutes if out.multi and index == 0 else ""
        )
        if material is None:
            view.hint = "calc.spool.choose_material"
        elif not options and not ctx.spool_problem:
            view.hint = (
                "calc.spool.none_filtered"
                if filter_active(ctx, draft)
                else "calc.spool.none_for_material"
            )
        if not show_errors or (row.blank() and not (strict and index == 0)):
            view.slots = _slots(index, row, options, -1, False, errors)
            continue
        filled += 1
        if (table or strict) and material is None:
            fail(f"material_{index}", "calc.error.material", row.material)
        if grams is None:
            fail(f"grams_{index}", "calc.error.number", row.grams)
        minutes = parse_decimal(view.minutes_shown) if shared_time else print_minutes
        if minutes is None and shared_time:
            fail(f"minutes_{index}", "calc.error.number", view.minutes_shown)
        lines, last, done = _allocate(errors, index, row, grams, options, ctx)
        view.slots = _slots(index, row, options, last, done, errors)
        incomplete = not done and not _has_error(errors, index) and grams is not None
        if incomplete and not view.hint and strict:
            view.note = "calc.spool.incomplete"
        if not done:
            complete = False
        materials += [MaterialLine(take, spool.cost_per_kg) for spool, take in lines]
        if material is not None and minutes is not None:
            power.append(PowerLine(minutes, table[material]))
            if grams is not None and done:
                planned.append(
                    PlannedMaterial(
                        material,
                        grams,
                        minutes,
                        table[material],
                        [
                            PlannedSpool(spool.id, spool_name(spool), take, spool.cost_per_kg)
                            for spool, take in lines
                        ],
                    )
                )
    if out.multi and shared_time:
        shares = [parse_decimal(item.minutes_shown) for item in out.rows if not item.row.blank()]
        total = sum((share for share in shares if share is not None), ZERO)
        out.minutes_sum = (
            input_text(total, ctx.language),
            input_text(print_minutes, ctx.language) if print_minutes is not None else "",
        )
    if show_errors and filled == 0:
        fail("material_0", "calc.error.material", "")
    if errors or missing or not complete or filled == 0 or ctx.spool_problem:
        return out
    try:
        entry = CalculationInput(
            materials=materials,
            print_minutes=print_minutes,
            pieces_per_plate=pieces,
            power_lines=power,
            power_price=ctx.settings.electricity_price,
            machine_rate=ctx.rates[printer_id],
            scrap_percent=numbers["scrap"],
            setup_minutes=numbers["setup_minutes"],
            rework_minutes=numbers["rework_minutes"],
            labour_rate=ctx.settings.labour_rate,
            margin_percent=numbers["margin"],
            rounding=ctx.settings.rounding,
            target_price=ctx.target_price,
        )
        result = calculate(entry)
    except CalculationError:
        errors["print_minutes"] = "calc.error.number"
        return out
    out.result = shown_result(result, ctx.language)
    out.plan = Plan(
        printer_id=printer_id,
        printer_name=ctx.names.get(printer_id, ""),
        print_minutes=print_minutes,
        pieces=pieces,
        setup_minutes=numbers["setup_minutes"],
        rework_minutes=numbers["rework_minutes"],
        scrap_percent=numbers["scrap"],
        margin_percent=numbers["margin"],
        power_price=ctx.settings.electricity_price,
        machine_rate=ctx.rates[printer_id],
        labour_rate=ctx.settings.labour_rate,
        rounding=str(ctx.settings.rounding),
        target_price=ctx.target_price,
        materials=planned,
        result=result,
    )
    return out


def _default(value: Decimal, language: str) -> str:
    return format_decimal(value, language)


def _canonical(table: dict[str, Decimal], text: str) -> str | None:
    """The material as the table spells it, or None when the table has no such material."""
    wanted = printer_models.same_material(text)
    for name in table:
        if printer_models.same_material(name) == wanted and wanted:
            return name
    return None


def _slots(
    index: int,
    row: Row,
    options: list[tuple[str, str]],
    last: int,
    done: bool,
    errors: dict[str, str],
) -> list[Slot]:
    """The spool selects: up to the last one that counts, one more while the position is not
    complete, and every field with an error (so that the message has a place)."""
    count = last + 1 + (0 if done else 1)
    for slot in range(MAX_SPOOLS):
        if f"spool_{index}_{slot}" in errors:
            count = max(count, slot + 1)
    count = min(max(count, 1), MAX_SPOOLS)
    return [
        Slot(f"spool_{index}_{slot}", row.spools[slot] if slot < len(row.spools) else "", options)
        for slot in range(count)
    ]


def shown_result(result: CalculationResult, language: str) -> dict[str, str]:
    wage = result.hourly_wage
    return {
        "material": cents(result.material_per_plate, language),
        "power": cents(result.power_per_plate, language),
        "machine": cents(result.machine_per_plate, language),
        "labour": cents(result.labour_cost, language),
        "cost_without": cents(result.cost_without_labour, language),
        "cost_with": cents(result.cost_with_labour, language),
        "suggested": cents(result.suggested_price, language),
        "selling": cents(result.selling_price, language),
        "profit": cents(result.profit, language),
        "wage": NO_VALUE if wage is None else cents(wage, language),
    }
