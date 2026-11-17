"""Calculation with a .gcode.3mf file (issue #181, part 2): checking the upload, the plates of a
file and the values that are put into the form.

Nothing is stored: the file is read from memory by `slice_reader` and forgotten. The name of the
upload is only shown (cleaned), never used as a path. What the person picks on the plate page
travels as a small JSON text in the form (the numbers are only a suggestion for fields that stay
editable, and they are checked again like typed values).
"""

import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app import slice_reader
from app.calc_form import NO_VALUE, Row
from app.production_form import input_text
from app.slice_reader import SliceFile, SliceFileError

MAX_UPLOAD = 50 * 1024 * 1024
# What a form may weigh besides the file (the other fields)
FORM_SLACK = 1024 * 1024
MAX_NAME = 80
MAX_PAYLOAD = 200_000
MAX_PLATES = 50
MAX_FILAMENTS = 20
MAX_TEXT = 40
MAX_SECONDS = slice_reader.MAX_SECONDS
MAX_GRAMS = slice_reader.MAX_GRAMS
SUFFIX = ".gcode.3mf"
_WHOLE = re.compile(r"[0-9]{1,9}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
CENT = Decimal("0.01")
ZERO = Decimal(0)
_ERRORS = {
    "not_sliced": "calc.file.error.not_sliced",
    "too_large": "calc.file.error.too_large",
}


def body_limit() -> int:
    return MAX_UPLOAD + FORM_SLACK


def clean_name(raw: str | None) -> str:
    """The name for showing: no folders, no control characters, not too long."""
    name = _CONTROL.sub("", (raw or "").replace("\\", "/").rsplit("/", 1)[-1]).strip()
    return name[:MAX_NAME]


def read_upload(data: bytes, name: str) -> tuple[SliceFile | None, str | None]:
    """The content of the upload, or the translation key of why it cannot be used. The type
    comes from the content; the name must also end with .gcode.3mf."""
    if len(data) > MAX_UPLOAD:
        return None, "calc.file.error.too_large"
    if not data:
        return None, "calc.file.error.invalid"
    try:
        found = slice_reader.read_slice_file(data, MAX_UPLOAD)
    except SliceFileError as error:
        return None, _ERRORS.get(error.code, "calc.file.error.invalid")
    if data[:4] != b"PK\x03\x04":
        # A plain G-code file is readable, but only the .gcode.3mf holds all we need
        return None, "calc.file.error.gcode"
    if not name.lower().endswith(SUFFIX):
        return None, "calc.file.error.name"
    return found, None


@dataclass(frozen=True)
class FilamentView:
    material: str
    color: str
    grams: str
    minutes: str


@dataclass(frozen=True)
class PlateView:
    index: int
    minutes: str
    objects: int | None
    estimated: bool
    filaments: list[FilamentView]
    multi: bool


def _minutes(seconds: int) -> Decimal:
    return (Decimal(seconds) / 60).quantize(CENT, rounding=ROUND_HALF_UP)


def payload_of(found: SliceFile) -> str:
    """The plates as the small text that travels in the form."""
    plates = [
        {
            "i": plate.index,
            "s": plate.seconds,
            "o": plate.objects,
            "e": plate.times_estimated,
            "f": [
                [(f.material or "")[:MAX_TEXT], (f.color or "")[:MAX_TEXT], str(f.grams), f.seconds]
                for f in plate.filaments[:MAX_FILAMENTS]
            ],
        }
        for plate in found.plates[:MAX_PLATES]
    ]
    return json.dumps({"p": plates}, separators=(",", ":"))


@dataclass(frozen=True)
class Plate:
    index: int
    seconds: int
    objects: int | None
    estimated: bool
    # (material, color, grams, seconds)
    filaments: list[tuple[str, str, Decimal, int]]


def _whole(value: object, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
        raise ValueError("number")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("text")
    return _CONTROL.sub("", value)[:MAX_TEXT]


def plates_of(payload: str) -> list[Plate]:
    """The plates from the text of the form. Raises ValueError for anything unexpected."""
    if not payload or len(payload) > MAX_PAYLOAD:
        raise ValueError("size")
    try:
        data = json.loads(payload)
    except (ValueError, RecursionError):
        raise ValueError("json") from None
    items = data.get("p") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items or len(items) > MAX_PLATES:
        raise ValueError("plates")
    plates: list[Plate] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("f"), list):
            raise ValueError("plate")
        if not item["f"] or len(item["f"]) > MAX_FILAMENTS:
            raise ValueError("filaments")
        objects = item.get("o")
        filaments = []
        for entry in item["f"]:
            if not isinstance(entry, list) or len(entry) != 4:
                raise ValueError("filament")
            try:
                grams = Decimal(entry[2])
            except (InvalidOperation, TypeError):
                raise ValueError("grams") from None
            if not grams.is_finite() or not 0 <= grams < MAX_GRAMS:
                raise ValueError("grams")
            filaments.append(
                (_text(entry[0]), _text(entry[1]), grams, _whole(entry[3], MAX_SECONDS))
            )
        plates.append(
            Plate(
                _whole(item.get("i"), 10**6),
                _whole(item.get("s"), MAX_SECONDS),
                None if objects is None else _whole(objects, 10**6),
                item.get("e") is True,
                filaments,
            )
        )
    if len({plate.index for plate in plates}) != len(plates):
        raise ValueError("duplicate")
    return plates


def views_of(plates: list[Plate], language: str) -> list[PlateView]:
    shown = []
    for plate in plates:
        used = plate.filaments
        shown.append(
            PlateView(
                plate.index,
                input_text(_minutes(plate.seconds), language),
                plate.objects,
                plate.estimated and len(used) > 1,
                [
                    FilamentView(
                        material or NO_VALUE,
                        color,
                        input_text(grams, language),
                        input_text(_minutes(seconds), language),
                    )
                    for material, color, grams, seconds in used
                ],
                len(used) > 1,
            )
        )
    return shown


def chosen(plates: list[Plate], numbers: list[str]) -> list[Plate] | None:
    """The plates with these numbers (in the order of the file), or None when none is chosen or
    one does not exist."""
    wanted: set[int] = set()
    for text in numbers:
        if not _WHOLE.fullmatch(text):
            return None
        wanted.add(int(text))
    found = [plate for plate in plates if plate.index in wanted]
    return found if found and len(found) == len(wanted) else None


def merged(plates: list[Plate], language: str) -> tuple[str, list[Row], str]:
    """The print time (minutes), the material rows and the list of plate numbers of the chosen
    plates, added up. The same material and colour on several plates is one row. Filaments
    without grams are left out (nothing is calculated with 0)."""
    seconds = sum(plate.seconds for plate in plates)
    totals: dict[tuple[str, str], list] = {}
    for plate in plates:
        for material, color, grams, part in plate.filaments:
            if grams <= 0:
                continue
            entry = totals.setdefault(
                (material.casefold(), color.casefold()), [material, color, ZERO, 0]
            )
            entry[2] += grams
            entry[3] += part
    several = len(totals) > 1
    rows = [
        Row(
            material=material,
            grams=input_text(grams, language),
            minutes=input_text(_minutes(part), language) if several else "",
            color=color,
            file_material=material,
        )
        for material, color, grams, part in totals.values()
    ]
    return (
        input_text(_minutes(seconds), language),
        rows or [Row()],
        ", ".join(str(plate.index) for plate in plates),
    )


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def printer_matches(file_model: str, device_name: str, model_name: str | None) -> bool:
    """Whether the printer of the file (short model, e.g. "X1E") fits the chosen printer: its
    model or its name contains the model of the file. No model in the file: no objection."""
    wanted = _norm(file_model)
    if not wanted:
        return True
    return any(wanted in _norm(name or "") for name in (model_name, device_name))
