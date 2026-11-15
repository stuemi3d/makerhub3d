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

from app.models import Article
from app.print_list import PrintRow
from app.production import PrintInput, ResultInput

MAX_PRINTS = 100
MAX_NOTE = 500
COST_PARTS = ("material", "electricity", "wear")
_DECIMAL = re.compile(r"\d{1,9}(?:[.,]\d{1,4})?")
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
    action = text("action")
    draft.action = action if action in ("preview", "book") else "preview"
    return draft


@dataclass
class Checked:
    errors: dict[str, str] = field(default_factory=dict)
    prints: list[PrintInput] = field(default_factory=list)
    result: ResultInput | None = None
    booked_on: date | None = None


def _cost(draft: Draft, row: PrintRow, part: str, errors: dict[str, str]) -> Decimal:
    sent = draft.costs.get(row.archive_id, {}).get(part)
    if sent is None:
        # Not sent (first visit or a missing field): what Bambuddy says, empty = 0
        shown = {
            "material": row.material_value,
            "electricity": row.electricity_value,
            "wear": row.wear_value,
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

    if not draft.ids:
        errors["prints"] = "production.error.no_print"
    for archive_id in draft.ids:
        row = found.get(archive_id)
        if row is None or row.status not in SELECTABLE_STATUSES:
            errors["prints"] = "production.error.print_unusable"
            continue
        cost_errors: dict[str, str] = {}
        material = _cost(draft, row, "material", cost_errors)
        electricity = _cost(draft, row, "electricity", cost_errors)
        wear = _cost(draft, row, "wear", cost_errors)
        errors.update(cost_errors)
        checked.prints.append(PrintInput(archive_id, material, electricity, wear, row.failed))

    article = db.get(Article, int(draft.article)) if _WHOLE.fullmatch(draft.article) else None
    if article is None or not article.active:
        errors["article"] = "production.error.article"
    quantity = int(draft.quantity) if _WHOLE.fullmatch(draft.quantity) else 0
    if quantity <= 0:
        errors["quantity"] = "production.error.quantity"
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
    return checked
