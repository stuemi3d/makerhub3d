"""Spools and filaments (issue #236): what to propose for a spool. Nothing here touches the
database or Bambuddy; the pages pass in what they know.

A spool is proposed the filament that an earlier spool with exactly the same details (brand,
material, subtype, colour name) was assigned to; otherwise the filament with the same details;
otherwise (with a note) the one filament that only differs in a related brand ("Bambu" and
"Bambu Lab"). Spellings that only look alike ("Gray" and "Grey") are never joined: that stays
the decision of the person, who confirms every proposal.
"""

from dataclasses import dataclass

from app import filaments
from app.bambuddy import Spool


def spool_key(spool: Spool) -> str:
    return filaments.identity_key(
        spool.brand or "", spool.material, spool.subtype or "", spool.color_name or ""
    )


@dataclass(frozen=True)
class Candidate:
    """A filament as far as the proposal needs it."""

    id: int
    brand: str
    material: str
    subtype: str
    color_name: str

    @property
    def key(self) -> str:
        return filaments.identity_key(self.brand, self.material, self.subtype, self.color_name)


@dataclass(frozen=True)
class Proposal:
    filament_id: int | None
    # The brand of the proposed filament when it differs from the brand of the spool
    other_brand: str | None = None


def _words(text: str) -> str:
    return " ".join(text.split()).casefold()


def brands_related(spool_brand: str, filament_brand: str) -> bool:
    """Different brands where one is the beginning of the other at a word end: "Bambu" and
    "Bambu Lab". Only a hint for the person; nothing is joined by itself."""
    first, second = _words(spool_brand), _words(filament_brand)
    if not first or not second or first == second:
        return False
    return second.startswith(first + " ") or first.startswith(second + " ")


def propose(spool: Spool, candidates: list[Candidate], learned: dict[str, int]) -> Proposal:
    """What is proposed for a spool: the filament an earlier spool with exactly the same details
    was assigned to; else the filament with the same details; else the one filament that only
    differs in a related brand (with a note); else a new filament (filament_id None).

    `learned`: spool key of an assigned spool -> the filament it was assigned to (the newest
    assignment wins). Spellings that only look alike ("Gray"/"Grey") are never joined."""
    key = spool_key(spool)
    if key in learned:
        return Proposal(learned[key])
    for candidate in candidates:
        if candidate.key == key:
            return Proposal(candidate.id)
    rest = (_words(spool.material), _words(spool.subtype or ""), _words(spool.color_name or ""))
    similar = [
        c
        for c in candidates
        if (_words(c.material), _words(c.subtype), _words(c.color_name)) == rest
        and brands_related(spool.brand or "", c.brand)
    ]
    if len(similar) == 1:
        return Proposal(similar[0].id, similar[0].brand)
    return Proposal(None)


def new_filament_form(spool: Spool, color_name: str | None = None) -> dict[str, str]:
    """The form values for a new filament made from a spool. A colour name is only taken from
    the spool or typed by the person, never made up."""
    return {
        "brand": spool.brand or "",
        "material": spool.material,
        "subtype": spool.subtype or "",
        "color_name": spool.color_name or color_name or "",
        "color": spool.color or "",
        "standard_price": "",
        "min_stock_kg": "",
        "note": "",
    }


def spool_text(spool: Spool) -> str:
    """ "#12 Bambu Lab PLA Matte Ivory White": the number and what Bambuddy says."""
    name = " ".join(
        part for part in (spool.brand, spool.material, spool.subtype, spool.color_name) if part
    )
    return f"#{spool.id} {name}"
