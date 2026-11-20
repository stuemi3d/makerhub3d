"""Filaments (issue #235): reading and checking the form, the name and the identity key.

Nothing here touches the database.
"""

import re
from decimal import Decimal

TEXT_LIMIT = 50
NOTE_LIMIT = 500
FIELD_NAMES = (
    "brand",
    "material",
    "subtype",
    "color_name",
    "color",
    "standard_price",
    "min_stock_kg",
    "note",
)
SORT_KEYS = ("name", "brand", "material", "price", "status")

_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_MAX_INPUT_LENGTH = 20
PRICE_MAX = Decimal("100000")
STOCK_MAX = Decimal("100000")
MAX_DECIMALS = 2
STOCK_DECIMALS = 3


def _line(value: str) -> str:
    """One line of text without line breaks and with single spaces."""
    return " ".join(value.split())


def identity_key(brand: str, material: str, subtype: str, color_name: str) -> str:
    """Brand, material, subtype and colour name without case and extra spaces. Two filaments
    with the same key are the same filament."""
    return "|".join(_line(part).casefold() for part in (brand, material, subtype, color_name))


def display_name(brand: str, material: str, subtype: str, color_name: str) -> str:
    """ "Bambu Lab · PLA Matte · Ivory White": the material and the subtype belong together."""
    kind = " ".join(part for part in (material, subtype) if part)
    return " · ".join(part for part in (brand, kind, color_name) if part)


def _decimal(raw: str, places: int, maximum: Decimal) -> tuple[Decimal | None, str | None]:
    """An optional number (zero is allowed): None for empty text."""
    text = raw.strip()
    if not text:
        return None, None
    if text.startswith("-") and _NUMBER.fullmatch(text[1:]):
        return None, "settings.error.negative"
    if len(text) > _MAX_INPUT_LENGTH or not _NUMBER.fullmatch(text):
        return None, "settings.error.number"
    value = Decimal(text.replace(",", "."))
    if -value.as_tuple().exponent > places:
        return None, "settings.error.decimals"
    if value > maximum:
        return None, "settings.error.too_large"
    return value, None


def validate(raw: dict[str, str]) -> tuple[dict[str, object], dict[str, str]]:
    """The cleaned values and a mapping field name -> translation key of the error.

    Only the material is required. Whether the filament exists already is checked by the
    caller against the database (with `identity_key`).
    """
    values: dict[str, object] = {}
    errors: dict[str, str] = {}

    for name in ("brand", "material", "subtype", "color_name"):
        text = _line(raw.get(name, ""))
        if not text and name == "material":
            errors[name] = "filament.error.required"
        elif len(text) > TEXT_LIMIT:
            errors[name] = "filament.error.too_long"
        elif not text.isprintable():
            errors[name] = "filament.error.chars"
        else:
            values[name] = text

    color = raw.get("color", "").strip()
    if color and not _COLOR.fullmatch(color):
        errors["color"] = "filament.error.color"
    else:
        values["color"] = color.upper()

    for name, places, maximum in (
        ("standard_price", MAX_DECIMALS, PRICE_MAX),
        ("min_stock_kg", STOCK_DECIMALS, STOCK_MAX),
    ):
        value, error = _decimal(raw.get(name, ""), places, maximum)
        if error:
            errors[name] = error
        else:
            values[name] = value

    note = raw.get("note", "").replace("\r\n", "\n").strip()
    if len(note) > NOTE_LIMIT:
        errors["note"] = "filament.error.too_long"
    elif not all(char.isprintable() or char == "\n" for char in note):
        errors["note"] = "filament.error.chars"
    else:
        values["note"] = note
    return values, errors
