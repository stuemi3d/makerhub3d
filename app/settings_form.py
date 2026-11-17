"""Reading and checking the settings form. Nothing here touches the database."""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.models import AppSettings, Rounding

# Digits with at most one decimal separator (comma or dot); no exponent, no thousands separator.
_NUMBER = re.compile(r"-?[0-9]+(?:[.,][0-9]+)?")
_MAX_INPUT_LENGTH = 20
SPOOL_CATEGORY_MAX_LENGTH = 50


@dataclass(frozen=True)
class DecimalField:
    name: str
    max_decimals: int
    max_value: Decimal


DECIMAL_FIELDS = (
    DecimalField("electricity_price", 4, Decimal("100")),
    DecimalField("labour_rate", 2, Decimal("10000")),
    DecimalField("scrap_rate", 2, Decimal("100")),
    DecimalField("markup", 2, Decimal("1000")),
    DecimalField("deviation_threshold", 2, Decimal("100")),
)
FIELD_NAMES = (
    "electricity_price",
    "labour_rate",
    "scrap_rate",
    "markup",
    "rounding",
    "spool_category",
    "deviation_threshold",
)


def format_decimal(value: Decimal, language: str) -> str:
    text = str(value)
    return text.replace(".", ",") if language == "de" else text


def form_values(settings: AppSettings, language: str) -> dict[str, str]:
    """The stored values as the form shows them."""
    values = {f.name: format_decimal(getattr(settings, f.name), language) for f in DECIMAL_FIELDS}
    values["rounding"] = str(settings.rounding)
    values["spool_category"] = settings.spool_category
    return values


def _parse_decimal(raw: str, field: DecimalField) -> tuple[Decimal | None, str | None]:
    text = raw.strip()
    if not text:
        return None, "settings.error.required"
    if len(text) > _MAX_INPUT_LENGTH or not _NUMBER.fullmatch(text):
        return None, "settings.error.number"
    # Checked on the text, so that "-0" is rejected as well.
    if text.startswith("-"):
        return None, "settings.error.negative"
    value = Decimal(text.replace(",", "."))
    if -value.as_tuple().exponent > field.max_decimals:
        return None, "settings.error.decimals"
    if value > field.max_value:
        return None, "settings.error.too_large"
    return value, None


def validate(raw: dict[str, str]) -> tuple[dict[str, object], dict[str, str]]:
    """Checks the submitted form.

    Returns the cleaned values and a mapping field name -> translation key of the error.
    Only when the error mapping is empty may the values be saved.
    """
    values: dict[str, object] = {}
    errors: dict[str, str] = {}

    for field in DECIMAL_FIELDS:
        value, error = _parse_decimal(raw.get(field.name, ""), field)
        if error:
            errors[field.name] = error
        else:
            values[field.name] = value

    rounding = raw.get("rounding", "")
    if rounding in {choice.value for choice in Rounding}:
        values["rounding"] = Rounding(rounding)
    else:
        errors["rounding"] = "settings.error.choice"

    category = raw.get("spool_category", "").strip()
    if not category:
        errors["spool_category"] = "settings.error.required"
    elif len(category) > SPOOL_CATEGORY_MAX_LENGTH:
        errors["spool_category"] = "settings.error.too_long"
    else:
        values["spool_category"] = category

    return values, errors
