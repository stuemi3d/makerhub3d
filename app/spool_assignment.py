"""Spools and filaments (issue #236): what to propose for a spool. Nothing here touches the
database or Bambuddy; the pages pass in what they know.

A spool is proposed the filament that an earlier spool with exactly the same details (brand,
material, subtype, colour name) was assigned to; otherwise the filament with the same details.
Spellings that only look alike ("Gray" and "Grey") are never joined: that stays the decision
of the person, who confirms every proposal.
"""

from app import filaments
from app.bambuddy import Spool


def spool_key(spool: Spool) -> str:
    return filaments.identity_key(
        spool.brand or "", spool.material, spool.subtype or "", spool.color_name or ""
    )


def propose(spool: Spool, by_identity: dict[str, int], learned: dict[str, int]) -> int | None:
    """The id of the proposed filament, or None (then a new filament is proposed).

    `by_identity`: identity key of a filament -> its id. `learned`: spool key of an assigned
    spool -> the filament it was assigned to (the newest assignment wins)."""
    key = spool_key(spool)
    return learned.get(key) or by_identity.get(key)


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
