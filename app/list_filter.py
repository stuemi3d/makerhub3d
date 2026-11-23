"""Filter bar of the lists (issue #257): reading the filter from the address and describing the
bar (fields, chips, links) for the template `_filter_bar.html`.

The address is never trusted: an unknown choice means "no filter" (the default), text is
trimmed and cut. Filtering itself is done by the pages on the loaded rows; text is compared as
plain text (no wildcards), so special characters mean what they say.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

MAX_TEXT = 100


@dataclass(frozen=True)
class Field:
    """One filter. Without `options` it is a search text field, otherwise a choice.
    `default` is the choice that means "not filtered" (or the standard view)."""

    name: str
    label: str
    options: tuple[tuple[str, str], ...] = ()
    default: str = ""


def read(fields: tuple[Field, ...], source: Mapping[str, str]) -> dict[str, str]:
    """The valid filter values from the address parameters."""
    values = {}
    for field in fields:
        raw = (source.get(field.name) or "").strip()[:MAX_TEXT]
        if field.options and raw not in {value for value, _ in field.options}:
            raw = field.default
        values[field.name] = raw
    return values


def params(fields: tuple[Field, ...], values: Mapping[str, str]) -> dict[str, str]:
    """Only the values that differ from the default, for addresses."""
    return {f.name: values[f.name] for f in fields if values[f.name] != f.default}


def _address(path: str, *parts: Mapping[str, str]) -> str:
    merged: dict[str, str] = {}
    for part in parts:
        merged.update(part)
    return path + ("?" + urlencode(merged) if merged else "")


def build(
    path: str,
    fields: tuple[Field, ...],
    values: Mapping[str, str],
    translate: Callable[[str], str],
    keep: Mapping[str, str] | None = None,
) -> dict:
    """What the template needs. `keep` are address parts that stay (the sorting)."""
    keep = dict(keep or {})
    active = params(fields, values)
    shown = []
    chips = []
    for field in fields:
        choices = [(value, translate(label)) for value, label in field.options]
        shown.append(
            {
                "name": field.name,
                "label": translate(field.label),
                "value": values[field.name],
                "choices": choices,
            }
        )
        if field.name not in active:
            continue
        label_of = dict(choices)
        text = label_of.get(values[field.name], values[field.name])
        others = {name: value for name, value in active.items() if name != field.name}
        chips.append(
            {
                "name": field.name,
                "text": f"{translate(field.label)}: {text}",
                "href": _address(path, others, keep),
            }
        )
    return {
        "path": path,
        "fields": shown,
        "chips": chips,
        "active_count": len(chips),
        "reset_href": _address(path, keep),
        "hidden": keep,
    }


def matches(text: str, *haystacks: str) -> bool:
    """Whether the search text is part of any of the texts (case blind, no wildcards)."""
    needle = text.strip().casefold()
    return not needle or any(needle in haystack.casefold() for haystack in haystacks)
