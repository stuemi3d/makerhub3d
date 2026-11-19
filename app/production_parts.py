"""The parts mask of a production (issue #215): what is on a plate, how much of it is waste
(German "Ausschuss"), and what follows from that for the production.

A line is one part (or a group of equal parts) of one plate: name, quantity on the plate,
weight of one piece (if known) and the waste in pieces. Good parts = quantity - waste.
Pure calculation, no page and no database. Mistakes in input raise `PartsError` with the
translation key of the message.

Numbers: the waste quote of a plate is waste / quantity by weight when every line has a
weight (a heavy part counts more), else by pieces. A production of different parts of one
article has as many finished articles as the part with the fewest good pieces over all plates
(a part printed again on another plate counts with its name); a production of several equal
articles counts all good pieces.
"""

import json
import re
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

from app.archive_parts import ArchiveParts

MAX_LINES = 50
MAX_NAME = 80
MAX_PIECES = 1_000_000
MAX_GRAMS = Decimal(1_000_000)
MAX_DETECTED = 20_000
ZERO = Decimal(0)
FOUR = Decimal("0.0001")
_WHOLE = re.compile(r"\d{1,9}")
_DECIMAL = re.compile(r"\d{1,9}(?:[.,]\d{1,4})?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class PartsError(ValueError):
    """`code` is the translation key of the message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PartLine:
    name: str
    qty: int  # pieces of this part on the plate
    grams: Decimal | None  # one piece; None = unknown
    waste: int = 0

    @property
    def good(self) -> int:
        return self.qty - self.waste

    def check(self) -> None:
        if not 0 < self.qty <= MAX_PIECES:
            raise PartsError("production.error.parts_quantity")
        if not 0 <= self.waste <= self.qty:
            raise PartsError("production.error.parts_waste")
        if self.grams is not None and not 0 <= self.grams < MAX_GRAMS:
            raise PartsError("production.error.parts_grams")


def clean_name(text: str) -> str:
    return _CONTROL.sub(" ", text).strip()[:MAX_NAME]


def read_line(name: str, qty: str, grams: str, waste: str) -> PartLine:
    """A line from the texts of the form (comma or dot; empty weight = unknown, empty waste =
    0). Raises PartsError."""
    qty, grams, waste = qty.strip(), grams.strip(), waste.strip()
    if not _WHOLE.fullmatch(qty):
        raise PartsError("production.error.parts_quantity")
    if waste and not _WHOLE.fullmatch(waste):
        raise PartsError("production.error.parts_waste")
    if grams and not _DECIMAL.fullmatch(grams):
        raise PartsError("production.error.parts_grams")
    parsed = PartLine(
        clean_name(name),
        int(qty),
        Decimal(grams.replace(",", ".")) if grams else None,
        int(waste) if waste else 0,
    )
    parsed.check()
    return parsed


def _average(lines: list[PartLine]) -> Decimal | None:
    """The weight of one piece of several lines, weighted by quantity; None if one is unknown."""
    if not lines or any(x.grams is None for x in lines):
        return None
    pieces = sum(x.qty for x in lines)
    return (sum(x.grams * x.qty for x in lines) / pieces).quantize(FOUR, ROUND_HALF_UP)


def merge(lines: list[PartLine], picked: list[int]) -> list[PartLine]:
    """The picked lines as one (name of the first, quantities and waste added), at the place of
    the first. Fewer than two valid picks change nothing."""
    chosen = sorted(set(picked))
    if len(chosen) < 2 or chosen[0] < 0 or chosen[-1] >= len(lines):
        return lines
    group = [lines[i] for i in chosen]
    joined = PartLine(
        group[0].name,
        sum(x.qty for x in group),
        _average(group),
        sum(x.waste for x in group),
    )
    result = list(lines)
    result[chosen[0]] = joined
    return [x for i, x in enumerate(result) if i == chosen[0] or i not in chosen]


def split(lines: list[PartLine], index: int) -> list[PartLine]:
    """A line with quantity n as n lines with quantity 1; the waste goes to the first ones."""
    if not 0 <= index < len(lines) or lines[index].qty < 2:
        return lines
    source = lines[index]
    pieces = [replace(source, qty=1, waste=1 if n < source.waste else 0) for n in range(source.qty)]
    return [*lines[:index], *pieces, *lines[index + 1 :]]


def same_article(lines: list[PartLine]) -> list[PartLine]:
    """All lines of a plate as one (several equal articles)."""
    return merge(lines, list(range(len(lines)))) if len(lines) > 1 else list(lines)


def quote(lines: list[PartLine]) -> tuple[Decimal, bool] | None:
    """(waste quote as a fraction, whether it was counted by pieces); None without pieces."""
    if all(x.grams is not None for x in lines):
        weighted = sum((x.grams * x.qty for x in lines), ZERO)
        if weighted > 0:
            wasted = sum((x.grams * x.waste for x in lines), ZERO)
            return wasted / weighted, False
    pieces = sum(x.qty for x in lines)
    if pieces <= 0:
        return None
    return Decimal(sum(x.waste for x in lines)) / pieces, True


def totals(plates: list[list[PartLine]]) -> tuple[int, int]:
    """(good pieces, waste pieces) over all plates."""
    lines = [x for plate in plates for x in plate]
    return sum(x.good for x in lines), sum(x.waste for x in lines)


def finished_articles(plates: list[list[PartLine]]) -> int:
    """The good pieces of the scarcest part over all plates (equal names count together)."""
    good: dict[str, int] = {}
    for number, plate in enumerate(plates):
        for index, x in enumerate(plate):
            # Parts without a name cannot be told apart: each one counts for itself
            key = x.name.casefold() or f"\0{number}:{index}"
            good[key] = good.get(key, 0) + x.good
    return min(good.values()) if good else 0


def detect(found: ArchiveParts | None) -> tuple[list[PartLine], str]:
    """The lines of a plate from what Bambuddy and the file say, and the mode to preselect
    ("same" for one line, "different" for several, "" when nothing is known)."""
    if found is None:
        return [], ""
    lines: list[PartLine]
    if found.parts:
        lines = [
            PartLine(clean_name(p.name), p.quantity, p.grams_each.quantize(FOUR, ROUND_HALF_UP))
            for p in found.parts
        ]
    else:
        counts: dict[str, int] = {}
        for name in found.names:
            key = clean_name(name)
            counts[key] = counts.get(key, 0) + 1
        lines = [PartLine(name, qty, None) for name, qty in counts.items()]
    lines = lines[:MAX_LINES]
    if not lines:
        return [], ""
    return lines, "same" if len(lines) == 1 else "different"


def stored_detected(lines: list[PartLine]) -> str:
    """The detected lines as text for a hidden field of the form."""
    return json.dumps(
        [[x.name, str(x.qty), "" if x.grams is None else str(x.grams)] for x in lines],
        ensure_ascii=False,
    )


def parse_detected(text: str) -> list[PartLine]:
    """The lines from `stored_detected`; anything odd gives an empty list (the form can be
    tampered with)."""
    if not text or len(text) > MAX_DETECTED:
        return []
    try:
        items = json.loads(text)
    except (ValueError, RecursionError):
        return []
    if not isinstance(items, list) or len(items) > MAX_LINES:
        return []
    lines: list[PartLine] = []
    for item in items:
        if not (
            isinstance(item, list) and len(item) == 3 and all(isinstance(v, str) for v in item)
        ):
            return []
        try:
            lines.append(read_line(item[0], item[1], item[2], ""))
        except PartsError:
            return []
    return lines
