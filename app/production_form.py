"""The form for a new production (issues #127, #140): reading, checking and showing what was sent.

The form is one page for three steps (preview, book, show errors); everything the user typed
comes back into it. A production has exactly one result article. Numbers are typed with a comma
or a dot, at most 4 decimals.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.models import AppSettings, Article
from app.print_list import PrintRow
from app.production import MAX_PIECES, LaborInput, PrintInput, ResultInput

MAX_PRINTS = 100
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


def machine_default(seconds: int | None, rate: Decimal) -> Decimal | None:
    """Print hours x hourly machine rate, to 4 decimals; None when there is no duration or the
    rate is 0 (then the field stays empty and counts as 0)."""
    if seconds is None or rate <= 0:
        return None
    return (Decimal(seconds) / 3600 * rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def machine_rate(db: Session) -> Decimal:
    settings = db.get(AppSettings, 1)
    return settings.machine_rate if settings else Decimal(0)


def machine_hint(row: PrintRow, rate: Decimal) -> str | None:
    """The translation key of why the machine field is empty, or None."""
    if row.seconds is None:
        return "production.machine_missing.duration"
    if rate <= 0:
        return "production.machine_missing.rate"
    return None


@dataclass
class Draft:
    """What the form sent, as text."""

    ids: list[int] = field(default_factory=list)
    # Per print id and part: the text, or None when the form did not send it (then the value
    # from Bambuddy counts)
    costs: dict[int, dict[str, str | None]] = field(default_factory=dict)
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


def _ids(values: list[str]) -> list[int]:
    found: list[int] = []
    for text in values[:MAX_PRINTS]:
        if re.fullmatch(r"[1-9][0-9]{0,8}", text.strip()) and int(text) not in found:
            found.append(int(text))
    return found


def read(form: FormData | None, ids: list[str] | None = None) -> Draft:
    """The draft from a POST form (or from the chosen ids only, for the first visit)."""
    if form is None:
        # First visit: only the chosen prints are known
        return Draft(ids=_ids(ids or []))
    draft = Draft(ids=_ids([str(v) for v in form.getlist("prints")]))

    def text(name: str) -> str | None:
        value = form.get(name)
        return value if isinstance(value, str) else None

    for archive_id in draft.ids:
        draft.costs[archive_id] = {part: text(f"cost_{part}_{archive_id}") for part in COST_PARTS}
    draft.article = (text("article") or "").strip()
    draft.quantity = (text("quantity") or "").strip()
    draft.date = (text("date") or "").strip()
    draft.note = (text("note") or "").strip()
    draft.failed_pieces = (text("failed_pieces") or "").strip()
    action = text("action")
    draft.labor_minutes = (text("labor_minutes") or "").strip() if "labor_minutes" in form else None
    draft.labor_rate = (text("labor_rate") or "").strip() if "labor_rate" in form else None
    draft.action = action if action in ("preview", "book") else "preview"
    return draft


@dataclass
class Checked:
    errors: dict[str, str] = field(default_factory=dict)
    prints: list[PrintInput] = field(default_factory=list)
    result: ResultInput | None = None
    booked_on: date | None = None
    labor: LaborInput | None = None
    failed_pieces: int = 0


def _cost(draft: Draft, row: PrintRow, part: str, errors: dict[str, str], rate: Decimal) -> Decimal:
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


def check(db: Session, draft: Draft, found: dict[int, PrintRow], today: date) -> Checked:
    """Checks what the form can say by itself. `found` are the prints Bambuddy knows."""
    checked = Checked()
    errors = checked.errors
    rate = machine_rate(db)

    if not draft.ids:
        errors["prints"] = "production.error.no_print"
    for archive_id in draft.ids:
        row = found.get(archive_id)
        if row is None or row.status not in SELECTABLE_STATUSES:
            errors["prints"] = "production.error.print_unusable"
            continue
        cost_errors: dict[str, str] = {}
        material = _cost(draft, row, "material", cost_errors, rate)
        electricity = _cost(draft, row, "electricity", cost_errors, rate)
        machine = _cost(draft, row, "machine", cost_errors, rate)
        errors.update(cost_errors)
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
