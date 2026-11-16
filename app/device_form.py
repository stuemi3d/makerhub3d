"""Reading and checking the device form (issue #157). Nothing here touches the database."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

KINDS = ("printer", "dryer")
NAME_MAX_LENGTH = 100
FIELD_NAMES = ("name", "bought_on", "price", "years", "hours_per_year", "maintenance_pct")

_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MAX_INPUT_LENGTH = 20


@dataclass(frozen=True)
class Field:
    name: str
    max_value: Decimal
    allow_zero: bool = False


PRICE = Field("price", Decimal("1000000"))
YEARS = Field("years", Decimal("100"))
HOURS = Field("hours_per_year", Decimal("8760"))
MAINTENANCE = Field("maintenance_pct", Decimal("1000"), allow_zero=True)
MAX_DECIMALS = 2


def _parse(raw: str, field: Field) -> tuple[Decimal | None, str | None]:
    text = raw.strip()
    if not text:
        return None, "settings.error.required"
    if text.startswith("-") and _NUMBER.fullmatch(text[1:]):
        return None, "settings.error.negative"
    if len(text) > _MAX_INPUT_LENGTH or not _NUMBER.fullmatch(text):
        return None, "settings.error.number"
    value = Decimal(text.replace(",", "."))
    if -value.as_tuple().exponent > MAX_DECIMALS:
        return None, "settings.error.decimals"
    if value > field.max_value:
        return None, "settings.error.too_large"
    if value == 0 and not field.allow_zero:
        return None, "device.error.zero"
    return value, None


def validate(kind: str, raw: dict[str, str]) -> tuple[dict[str, object], dict[str, str]]:
    """Checks the submitted form of a printer or a dryer.

    Returns the cleaned values and a mapping field name -> translation key of the error.
    Only when the error mapping is empty may the values be saved. A dryer has no print hours.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind of device: {kind!r}")
    values: dict[str, object] = {}
    errors: dict[str, str] = {}

    name = raw.get("name", "").strip()
    if not name:
        errors["name"] = "settings.error.required"
    elif len(name) > NAME_MAX_LENGTH:
        errors["name"] = "device.error.name_too_long"
    else:
        values["name"] = name

    bought_on = raw.get("bought_on", "").strip()
    try:
        if not _ISO_DATE.fullmatch(bought_on):
            raise ValueError
        values["bought_on"] = date.fromisoformat(bought_on)
    except ValueError:
        errors["bought_on"] = "device.error.date"

    fields = [PRICE, YEARS, MAINTENANCE] + ([HOURS] if kind == "printer" else [])
    for field in fields:
        value, error = _parse(raw.get(field.name, ""), field)
        if error:
            errors[field.name] = error
        else:
            values[field.name] = value
    if kind == "dryer":
        values["hours_per_year"] = None
    return values, errors
