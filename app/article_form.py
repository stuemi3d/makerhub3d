"""Reading and checking the article form. Nothing here touches the database."""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.settings_form import format_decimal

# Digits with at most one decimal separator (comma or dot); no exponent, no thousands separator.
_NUMBER = re.compile(r"-?[0-9]+(?:[.,][0-9]+)?")
_MAX_INPUT_LENGTH = 20
NUMBER_MAX_LENGTH = 50
NAME_MAX_LENGTH = 100
MAX_MIN_STOCK = 1_000_000
FIELD_NAMES = ("number", "name", "selling_price", "weight", "min_stock")


@dataclass(frozen=True)
class DecimalField:
    name: str
    max_decimals: int
    max_value: Decimal
    # A weight of 0 would make a piece cost nothing when costs are split.
    allow_zero: bool


DECIMAL_FIELDS = (
    DecimalField("selling_price", 2, Decimal("100000"), allow_zero=True),
    DecimalField("weight", 2, Decimal("1000"), allow_zero=False),
)


def number_key(number: str) -> str:
    """The form of an article number used to compare them (case does not matter)."""
    return number.casefold()


def _parse_number(text: str) -> tuple[Decimal | None, str | None]:
    if len(text) > _MAX_INPUT_LENGTH or not _NUMBER.fullmatch(text):
        return None, "article.error.number"
    # Checked on the text, so that "-0" is rejected as well.
    if text.startswith("-"):
        return None, "article.error.negative"
    return Decimal(text.replace(",", ".")), None


def _parse_decimal(raw: str, field: DecimalField) -> tuple[Decimal | None, str | None]:
    value, error = _parse_number(raw)
    if error:
        return None, error
    if -value.as_tuple().exponent > field.max_decimals:
        return None, "article.error.decimals"
    if value > field.max_value:
        return None, "article.error.too_large"
    if value == 0 and not field.allow_zero:
        return None, "article.error.zero"
    return value, None


def _parse_whole(raw: str) -> tuple[int | None, str | None]:
    value, error = _parse_number(raw)
    if error:
        return None, error
    if value != value.to_integral_value() or not re.fullmatch(r"[0-9]+", raw):
        return None, "article.error.whole"
    if value > MAX_MIN_STOCK:
        return None, "article.error.too_large"
    return int(value), None


def validate(raw: dict[str, str]) -> tuple[dict[str, object], dict[str, str]]:
    """Checks the submitted form.

    Returns the cleaned values and a mapping field name -> translation key of the error.
    Only when the error mapping is empty may the values be saved. Uniqueness of the number
    is checked against the database by the caller.
    """
    values: dict[str, object] = {}
    errors: dict[str, str] = {}

    for name, limit in (("number", NUMBER_MAX_LENGTH), ("name", NAME_MAX_LENGTH)):
        text = raw.get(name, "").strip()
        if not text:
            errors[name] = "article.error.required"
        elif len(text) > limit or any(not char.isprintable() for char in text):
            errors[name] = "article.error.too_long" if len(text) > limit else "article.error.chars"
        else:
            values[name] = text

    for field in DECIMAL_FIELDS:
        text = raw.get(field.name, "").strip()
        if not text:
            values[field.name] = None
            continue
        value, error = _parse_decimal(text, field)
        if error:
            errors[field.name] = error
        else:
            values[field.name] = value

    text = raw.get("min_stock", "").strip()
    if not text:
        values["min_stock"] = None
    else:
        whole, error = _parse_whole(text)
        if error:
            errors["min_stock"] = error
        else:
            values["min_stock"] = whole

    return values, errors


def form_values(values: dict[str, object], language: str) -> dict[str, str]:
    """Stored values as the form shows them (decimal comma in German; empty stays empty)."""
    shown = {}
    for name in FIELD_NAMES:
        value = values.get(name)
        if value is None:
            shown[name] = ""
        elif isinstance(value, Decimal):
            shown[name] = format_decimal(value.quantize(Decimal("0.01")), language)
        else:
            shown[name] = str(value)
    return shown
