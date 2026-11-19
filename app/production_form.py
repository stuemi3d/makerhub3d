"""The form for a new production (issues #127, #140): reading, checking and showing what was sent.

The form is one page for three steps (preview, book, show errors); everything the user typed
comes back into it. A production has exactly one result article. Numbers are typed with a comma
or a dot, at most 4 decimals.
"""

import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app import devices
from app.models import AppSettings, Article, Device
from app.print_list import PrintRow
from app.production import MAX_PIECES, LaborInput, PrintInput, ResultInput
from app.production_parts import (
    MAX_LINES,
    PartLine,
    PartsError,
    finished_articles,
    merge,
    parse_detected,
    quote,
    read_line,
    same_article,
    split,
    totals,
)

MAX_PRINTS = 100
PARTS_MODES = ("same", "different")
MAX_NOTE = 500
COST_PARTS = ("material", "electricity", "machine")
_DECIMAL = re.compile(r"\d{1,9}(?:[.,]\d{1,4})?")
_LABOR = re.compile(r"\d{1,6}(?:[.,]\d{1,4})?")
_WHOLE = re.compile(r"\d{1,9}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Bambuddy statuses of prints that are over, so that they can be booked
SELECTABLE_STATUSES = frozenset({"completed", "failed", "cancelled"})


def parse_decimal(text: str) -> Decimal | None:
    """A non-negative number with at most 4 decimals, comma or dot; else None."""
    text = text.strip()
    return Decimal(text.replace(",", ".")) if _DECIMAL.fullmatch(text) else None


def input_text(value: Decimal | None, language: str) -> str:
    """A number as it is shown in an input field (no trailing zeros)."""
    if value is None:
        return ""
    # Bambuddy may deliver more decimals than the form accepts
    text = format(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP).normalize(), "f")
    return text.replace(".", ",") if language == "de" else text


def money_text(value: Decimal, language: str) -> str:
    """An amount with two decimals, or up to four when the cents are not exact."""
    text = format(value.quantize(Decimal("0.0001")), "f")
    while text.endswith("0") and len(text.rsplit(".", 1)[1]) > 2:
        text = text[:-1]
    return text.replace(".", ",") if language == "de" else text


def machine_default(seconds: int | None, rate: Decimal | None) -> Decimal | None:
    """Print hours x hourly machine rate, to 4 decimals; None when there is no duration or no
    rate (no printer chosen); the field then stays empty and counts as 0."""
    if seconds is None or rate is None or rate <= 0:
        return None
    return (Decimal(seconds) / 3600 * rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Printers:
    """The active printers a print can be booked on (issue #162), with their machine rate:
    hourly rate of the printer plus the drying surcharge of all devices."""

    # (device id, name) sorted by name
    choices: list[tuple[int, str]]
    # device id -> machine rate
    rates: dict[int, Decimal]
    # Bambuddy printer id -> device id
    linked: dict[int, int]

    def default_for(self, printer_id: int | None) -> int | None:
        return self.linked.get(printer_id) if printer_id is not None else None


def load_printers(db: Session) -> Printers:
    rows = list(db.scalars(select(Device).order_by(Device.name, Device.id)))
    surcharge = devices.active_surcharge(rows)
    printers = [row for row in rows if row.kind == "printer" and row.active]
    return Printers(
        choices=[(row.id, row.name) for row in printers],
        rates={
            row.id: devices.printer_rate(
                row.price, row.years, row.hours_per_year, row.maintenance_pct
            )
            + surcharge
            for row in printers
        },
        linked={row.bambuddy_printer_id: row.id for row in printers if row.bambuddy_printer_id},
    )


def chosen_device(draft: "Draft", row: PrintRow, printers: Printers) -> int | None:
    """The device of a print: what the form sent (when it is an active printer), else the one
    linked to the Bambuddy printer of the print. None = nothing usable chosen."""
    sent = draft.devices.get(row.archive_id)
    if sent is None:
        return printers.default_for(row.printer_id)
    if _WHOLE.fullmatch(sent) and int(sent) in printers.rates:
        return int(sent)
    return None


def machine_hint(row: PrintRow, device: int | None) -> str | None:
    """The translation key of why the machine field is empty, or None."""
    if row.seconds is None:
        return "production.machine_missing.duration"
    if device is None:
        return "production.machine_missing.device"
    return None


@dataclass
class RawLine:
    """One line of the parts mask as the form sent it (text)."""

    name: str = ""
    qty: str = ""
    grams: str = ""
    waste: str = ""
    picked: bool = False


def raw_line(line: PartLine, language: str) -> RawLine:
    return RawLine(line.name, str(line.qty), input_text(line.grams, language), str(line.waste))


def is_blank(raw: RawLine) -> bool:
    return not (raw.name or raw.qty or raw.grams or raw.waste)


def used_lines(raws: list[RawLine]) -> list[PartLine] | None:
    """The lines that were filled in (empty ones do not count), or None when one is invalid."""
    return parsed_lines([r for r in raws if not is_blank(r)])


def parsed_lines(raws: list[RawLine]) -> list[PartLine] | None:
    """The lines of a plate, or None when one of them is not valid."""
    try:
        return [read_line(r.name, r.qty, r.grams, r.waste) for r in raws]
    except PartsError:
        return None


@dataclass
class Draft:
    """What the form sent, as text."""

    ids: list[int] = field(default_factory=list)
    # Per print id and part: the text, or None when the form did not send it (then the value
    # from Bambuddy counts)
    costs: dict[int, dict[str, str | None]] = field(default_factory=dict)
    # Per print id: the chosen device as text, or None when the form did not send it (then the
    # device linked to the Bambuddy printer counts)
    devices: dict[int, str | None] = field(default_factory=dict)
    # The print whose device was just changed: its machine costs are proposed again
    changed: int | None = None
    article: str = ""
    quantity: str = ""
    date: str = ""
    note: str = ""
    # Labour: None when the form did not send it (then no time and the rate of the settings)
    labor_minutes: str | None = None
    labor_rate: str | None = None
    # Failed pieces (issue #148); empty = none
    failed_pieces: str = ""
    action: str = "preview"
    # The parts mask (issue #215): "" = not used, else one of PARTS_MODES; the lines per print;
    # the lines found in the file (as text, to bring them back); what to do with the lines
    # ("mode", "merge:ID", "split:ID:K", "add:ID", "drop:ID:K"); the values the quantity and
    # the waste had when last worked out from the lines (a field the user changed stays).
    parts_mode: str = ""
    parts_mode_was: str = ""  # the mode before this request (the lines follow a changed mode)
    parts: dict[int, list[RawLine]] = field(default_factory=dict)
    detected: dict[int, str] = field(default_factory=dict)
    parts_do: str = ""
    quantity_auto: str = ""
    failed_auto: str = ""


def _ids(values: list[str]) -> list[int]:
    found: list[int] = []
    for text in values[:MAX_PRINTS]:
        if re.fullmatch(r"[1-9][0-9]{0,8}", text.strip()) and int(text) not in found:
            found.append(int(text))
    return found


def _read_parts(form: FormData, draft: Draft) -> None:
    mode = form.get("parts_mode")
    draft.parts_mode = mode if isinstance(mode, str) and mode in PARTS_MODES else ""
    was = form.get("parts_mode_was")
    draft.parts_mode_was = was if isinstance(was, str) else ""
    for archive_id in draft.ids:
        raws: list[RawLine] = []
        for k in range(MAX_LINES):
            names = [f"p{part}_{archive_id}_{k}" for part in ("name", "qty", "grams", "waste")]
            if not any(name in form for name in names):
                break
            values = [form.get(name) for name in names]
            raws.append(
                RawLine(
                    *[(v if isinstance(v, str) else "").strip() for v in values],
                    picked=f"ppick_{archive_id}_{k}" in form,
                )
            )
        draft.parts[archive_id] = raws
        found = form.get(f"detected_{archive_id}")
        draft.detected[archive_id] = found if isinstance(found, str) else ""
    do = form.get("parts_do")
    draft.parts_do = do if isinstance(do, str) else ""
    for name in ("quantity_auto", "failed_auto"):
        value = form.get(name)
        setattr(draft, name, value.strip() if isinstance(value, str) else "")


def _do_ids(do: str) -> tuple[str, list[int]]:
    kind, *rest = do.split(":")
    return kind, [int(v) for v in rest if _WHOLE.fullmatch(v)]


def _carry_waste(old: list[PartLine], new: list[PartLine]) -> list[PartLine]:
    """The waste of the old lines, given to the first new line of the same name (at most its
    quantity)."""
    result = list(new)
    for line in old:
        left = line.waste
        for k, target in enumerate(result):
            if left and target.name.casefold() == line.name.casefold() and target.waste == 0:
                taken = min(left, target.qty)
                result[k] = replace(target, waste=taken)
                left -= taken
    return result


def apply_parts_action(draft: Draft, language: str) -> None:
    """Carries out what the form asked for in the mask: another mode, merging picked lines,
    splitting or dropping a line, adding an empty one. Lines that are not valid stay as typed."""
    kind, numbers = _do_ids(draft.parts_do)
    if draft.parts_mode not in PARTS_MODES:
        return
    if kind == "mode":
        if draft.parts_mode == draft.parts_mode_was:
            return  # "Apply" without another mode keeps what was typed
        for archive_id in draft.ids:
            lines = used_lines(draft.parts.get(archive_id, []))
            if draft.parts_mode == "same":
                new = same_article(lines) if lines else None
            else:
                # Back to the parts found in the file, if there are any; waste typed for a
                # part of the same name is kept
                new = parse_detected(draft.detected.get(archive_id, "")) or lines
                if new is not None and lines:
                    new = _carry_waste(lines, new)
            if new is not None:
                draft.parts[archive_id] = [raw_line(x, language) for x in new]
    elif kind in ("merge", "split", "drop", "add") and numbers and numbers[0] in draft.ids:
        archive_id = numbers[0]
        raws = draft.parts.get(archive_id, [])
        if kind == "add":
            if len(raws) < MAX_LINES:
                raws.append(RawLine())
            draft.parts[archive_id] = raws
            return
        if kind == "drop" and len(numbers) > 1 and 0 <= numbers[1] < len(raws):
            del raws[numbers[1]]
            return
        lines = parsed_lines(raws)
        if lines is None:
            return
        if kind == "merge":
            picks = [k for k, raw in enumerate(raws) if raw.picked]
            new = merge(lines, picks)
        elif len(numbers) > 1:
            new = split(lines, numbers[1])
        else:
            return
        draft.parts[archive_id] = [raw_line(x, language) for x in new]


def refresh_auto(draft: Draft, force: bool = False) -> None:
    """Works out the good parts and the waste from the lines and puts them into the fields
    "quantity" and "failed pieces", unless the user changed them since."""
    if draft.parts_mode not in PARTS_MODES:
        return
    plates = [used_lines(draft.parts.get(i, [])) for i in draft.ids]
    if any(plate is None for plate in plates) or not any(plates):
        return  # nothing valid to work out from
    good, waste = totals(plates)
    articles = good if draft.parts_mode == "same" else finished_articles(plates)
    new_quantity, new_failed = str(articles), str(waste) if waste else ""
    if force or not draft.quantity or draft.quantity == draft.quantity_auto:
        draft.quantity = new_quantity
    if force or not draft.failed_pieces or draft.failed_pieces == draft.failed_auto:
        draft.failed_pieces = new_failed
    draft.quantity_auto, draft.failed_auto = new_quantity, new_failed


def percent_text(fraction: Decimal, language: str) -> str:
    """A fraction as a percentage with at most one decimal ("80 %", "25,6 %")."""
    text = format((fraction * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f")
    text = text[:-2] if text.endswith(".0") else text
    return (text.replace(".", ",") if language == "de" else text) + " %"


def parts_view(draft: Draft, language: str, errors: dict[str, str]) -> dict[int, dict]:
    """What the template shows of the mask per print: the lines (with their errors) and the
    waste quote of the plate."""
    shown: dict[int, dict] = {}
    for archive_id in draft.ids:
        raws = draft.parts.get(archive_id, [])
        lines = used_lines(raws)
        found = quote(lines) if lines else None
        shown[archive_id] = {
            "lines": [
                {"raw": raw, "k": k, "error": errors.get(f"parts_{archive_id}_{k}")}
                for k, raw in enumerate(raws)
            ],
            "quote": percent_text(found[0], language) if found else None,
            "by_pieces": bool(found and found[1]),
        }
    return shown


def read(form: FormData | None, ids: list[str] | None = None, language: str = "de") -> Draft:
    """The draft from a POST form (or from the chosen ids only, for the first visit)."""
    if form is None:
        # First visit: only the chosen prints are known
        return Draft(ids=_ids(ids or []))
    draft = Draft(ids=_ids([str(v) for v in form.getlist("prints")]))
    _read_parts(form, draft)

    def text(name: str) -> str | None:
        value = form.get(name)
        return value if isinstance(value, str) else None

    for archive_id in draft.ids:
        draft.costs[archive_id] = {part: text(f"cost_{part}_{archive_id}") for part in COST_PARTS}
        draft.devices[archive_id] = text(f"device_{archive_id}")
    draft.article = (text("article") or "").strip()
    draft.quantity = (text("quantity") or "").strip()
    draft.date = (text("date") or "").strip()
    draft.note = (text("note") or "").strip()
    draft.failed_pieces = (text("failed_pieces") or "").strip()
    action = text("action")
    draft.labor_minutes = (text("labor_minutes") or "").strip() if "labor_minutes" in form else None
    draft.labor_rate = (text("labor_rate") or "").strip() if "labor_rate" in form else None
    draft.action = action if action in ("preview", "book", "device") else "preview"
    if form.get("parts_do") and draft.action == "preview":
        draft.action = "parts"
    changed = (text("changed") or "").strip()
    if draft.action == "device" and _WHOLE.fullmatch(changed) and int(changed) in draft.ids:
        draft.changed = int(changed)
        # Another printer, another proposal: forget the machine costs that were shown
        draft.costs[draft.changed]["machine"] = None
    if draft.parts_do:
        draft.action = "parts"
        apply_parts_action(draft, language)
    if draft.parts_mode in PARTS_MODES:
        for archive_id in draft.ids:
            if not draft.parts.get(archive_id):
                draft.parts[archive_id] = [RawLine()]  # a plate always has a line to type into
    refresh_auto(draft)
    return draft


@dataclass
class Checked:
    errors: dict[str, str] = field(default_factory=dict)
    prints: list[PrintInput] = field(default_factory=list)
    result: ResultInput | None = None
    booked_on: date | None = None
    labor: LaborInput | None = None
    failed_pieces: int = 0


def _cost(
    draft: Draft, row: PrintRow, part: str, errors: dict[str, str], rate: Decimal | None
) -> Decimal:
    sent = draft.costs.get(row.archive_id, {}).get(part)
    if sent is None:
        # Not sent (first visit or a missing field): what Bambuddy says, empty = 0
        shown = {
            "material": row.material_value,
            "electricity": row.electricity_value,
            "machine": machine_default(row.seconds, rate),
        }[part]
        return (shown or Decimal(0)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if not sent.strip():
        return Decimal(0)
    value = parse_decimal(sent)
    if value is None:
        errors[f"cost_{part}_{row.archive_id}"] = "production.error.cost_number"
        return Decimal(0)
    return value


def _waste_share(draft: Draft, row: PrintRow) -> Decimal:
    """The share of the print that is waste, from the lines of the mask (0 without the mask or
    with lines that are not valid; those are reported by `check` anyway)."""
    if draft.parts_mode not in PARTS_MODES:
        return Decimal(0)
    lines = used_lines(draft.parts.get(row.archive_id, []))
    found = quote(lines) if lines else None
    return found[0] if found else Decimal(0)


def check(db: Session, draft: Draft, found: dict[int, PrintRow], today: date) -> Checked:
    """Checks what the form can say by itself. `found` are the prints Bambuddy knows."""
    checked = Checked()
    errors = checked.errors
    printers = load_printers(db)

    if not draft.ids:
        errors["prints"] = "production.error.no_print"
    elif not printers.rates:
        errors["prints"] = "production.error.no_printer"
    for archive_id in draft.ids:
        row = found.get(archive_id)
        if row is None or row.status not in SELECTABLE_STATUSES:
            errors["prints"] = "production.error.print_unusable"
            continue
        cost_errors: dict[str, str] = {}
        device = chosen_device(draft, row, printers)
        if device is None:
            errors[f"device_{row.archive_id}"] = "production.error.device"
        rate = printers.rates.get(device) if device is not None else None
        material = _cost(draft, row, "material", cost_errors, rate)
        electricity = _cost(draft, row, "electricity", cost_errors, rate)
        machine = _cost(draft, row, "machine", cost_errors, rate)
        errors.update(cost_errors)
        waste_share = _waste_share(draft, row)
        checked.prints.append(
            PrintInput(
                archive_id,
                material,
                electricity,
                machine,
                row.failed,
                seconds=row.seconds,
                grams=row.grams,
                kwh=row.kwh,
                filament_type=row.filament_type,
                filament_color=row.filament_color,
                waste_share=waste_share,
            )
        )

    article = db.get(Article, int(draft.article)) if _WHOLE.fullmatch(draft.article) else None
    if article is None or not article.active:
        errors["article"] = "production.error.article"
    quantity = int(draft.quantity) if _WHOLE.fullmatch(draft.quantity) else 0
    if quantity <= 0:
        errors["quantity"] = "production.error.quantity"
    if not draft.failed_pieces:
        checked.failed_pieces = 0
    elif _WHOLE.fullmatch(draft.failed_pieces) and int(draft.failed_pieces) <= MAX_PIECES:
        checked.failed_pieces = int(draft.failed_pieces)
    else:
        errors["failed_pieces"] = "production.error.failed_pieces"
    if draft.parts_mode in PARTS_MODES:
        for archive_id in draft.ids:
            for k, raw in enumerate(draft.parts.get(archive_id, [])):
                if is_blank(raw):
                    continue  # an empty line is not used
                try:
                    read_line(raw.name, raw.qty, raw.grams, raw.waste)
                except PartsError as error:
                    errors[f"parts_{archive_id}_{k}"] = error.code
    if article is not None and "article" not in errors and "quantity" not in errors:
        checked.result = ResultInput(article.id, quantity)

    if draft.date:
        try:
            if not _ISO_DATE.fullmatch(draft.date):
                raise ValueError
            checked.booked_on = date.fromisoformat(draft.date)
        except ValueError:
            errors["date"] = "production.error.date"
        else:
            if checked.booked_on > today:
                errors["date"] = "production.error.date"
    else:
        checked.booked_on = today
    if len(draft.note) > MAX_NOTE:
        errors["note"] = "production.error.note_long"
    _labor(db, draft, checked)
    return checked


def _labor(db: Session, draft: Draft, checked: Checked) -> None:
    """Minutes (empty = 0) and the hourly rate (not sent = the one of the settings)."""
    values: dict[str, Decimal] = {}
    for name, sent in (("labor_minutes", draft.labor_minutes), ("labor_rate", draft.labor_rate)):
        if not sent:
            values[name] = Decimal(0)
        elif _LABOR.fullmatch(sent):
            values[name] = Decimal(sent.replace(",", "."))
        else:
            checked.errors[name] = "production.error.labor_number"
    if draft.labor_rate is None:
        settings = db.get(AppSettings, 1)
        values["labor_rate"] = settings.labour_rate if settings else Decimal(0)
    if "labor_minutes" not in checked.errors and "labor_rate" not in checked.errors:
        checked.labor = LaborInput(values["labor_minutes"], values["labor_rate"])
