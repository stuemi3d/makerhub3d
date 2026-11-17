"""Reading and checking the rows of a power table (issue #174). Nothing here touches the database.

The form sends numbered rows `material_N` / `kwh_N`; a row with both empty is skipped, so a
row is removed by emptying it.
"""

import re
from decimal import Decimal

from app.printer_models import same_material

MAX_ROWS = 40
MATERIAL_MAX_LENGTH = 50
MAX_DECIMALS = 3
MAX_KWH = Decimal("100")

_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
_MAX_INPUT_LENGTH = 12


def field_names() -> tuple[str, ...]:
    return tuple(f"{part}_{index}" for index in range(MAX_ROWS) for part in ("material", "kwh"))


def _parse_kwh(text: str) -> tuple[Decimal | None, str | None]:
    if not text:
        return None, "settings.error.required"
    if len(text) > _MAX_INPUT_LENGTH or not _NUMBER.fullmatch(text):
        return None, "settings.error.number"
    value = Decimal(text.replace(",", "."))
    if -value.as_tuple().exponent > MAX_DECIMALS:
        return None, "model.error.decimals"
    if value > MAX_KWH:
        return None, "settings.error.too_large"
    if value == 0:
        return None, "device.error.zero"
    return value, None


def validate_rows(raw: dict[str, str]) -> tuple[list[tuple[str, Decimal]], dict[str, str]]:
    """The cleaned (material, kWh per hour) rows and a mapping field name -> error key."""
    values: list[tuple[str, Decimal]] = []
    errors: dict[str, str] = {}
    seen: set[str] = set()
    for index in range(MAX_ROWS):
        material = raw.get(f"material_{index}", "").strip()
        text = raw.get(f"kwh_{index}", "").strip()
        if not material and not text:
            continue
        if not material:
            errors[f"material_{index}"] = "settings.error.required"
        elif len(material) > MATERIAL_MAX_LENGTH:
            errors[f"material_{index}"] = "model.error.material_too_long"
        elif same_material(material) in seen:
            errors[f"material_{index}"] = "model.error.material_twice"
        value, error = _parse_kwh(text)
        if error:
            errors[f"kwh_{index}"] = error
        if material:
            seen.add(same_material(material))
        if f"material_{index}" not in errors and value is not None:
            values.append((material, value))
    return values, errors
