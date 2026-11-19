"""Parts of an archive entry (issue #214): what Bambuddy reports itself comes first, the file is
only an addition.

From Bambuddy's own answer (`plates`): the names and the number of objects on the plate and the
grams by colour. A plate with one object is that one part, no file needed. For several objects
the weight of each part is worked out from the stored file (`plate_parts`) and is used only when
it agrees with Bambuddy: same number of objects and a total within `WEIGHT_CHECK` of the grams
Bambuddy reports. So a new slicer version or file format can never produce wrong weights; in
the worst case the weights per part are missing (`parts` is None) and are entered by hand.
Mistakes with the file never raise; an entry without a file gives None; other mistakes with the
`plates` answer (not reachable, rejected, ...) raise `BambuddyError`.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.bambuddy import ArchivePlate, BambuddyClient, BambuddyError
from app.plate_parts import PlatePart, parts_from_file

# The total of the parts may differ from Bambuddy's total by at most this share (2 %)
WEIGHT_CHECK = Decimal("0.02")
ZERO = Decimal(0)


@dataclass(frozen=True)
class ArchiveParts:
    names: list[str]  # the objects on the plate, as Bambuddy lists them
    object_count: int
    total_grams: Decimal  # as Bambuddy reports it
    parts: list[PlatePart] | None  # with weights; None when they could not be checked


def _total(plate: ArchivePlate) -> Decimal:
    if plate.filament_grams is not None and plate.filament_grams > 0:
        return plate.filament_grams
    return sum((f.grams for f in plate.filaments), ZERO)


def _single(plate: ArchivePlate, total: Decimal) -> ArchiveParts:
    colors: dict[str, Decimal] = {}
    for item in plate.filaments:
        colors[item.color] = colors.get(item.color, ZERO) + item.grams
    name = plate.objects[0] if plate.objects else "?"
    part = PlatePart(name, 1, total, colors)
    return ArchiveParts(plate.objects, 1, total, [part])


def archive_parts(client: BambuddyClient, archive_id: int) -> ArchiveParts | None:
    """The parts of the plate of an archive entry, or None when Bambuddy lists no objects."""
    try:
        plates = client.archive_plates(archive_id)
    except BambuddyError as error:
        if error.code == "not_found":  # Bambuddy keeps no file for this entry
            return None
        raise
    if len(plates) == 1 and plates[0].object_count == 1 and plates[0].objects:
        total = _total(plates[0])
        return _single(plates[0], total) if total > 0 else None
    parts = None
    plate = plates[0] if len(plates) == 1 else None
    try:
        found = parts_from_file(client.archive_file(archive_id))
    except BambuddyError:
        found = None
    if found is not None and plate is None:
        plate = next((p for p in plates if p.index == found.plate_index), None)
    if plate is None or plate.object_count < 1 or not plate.objects:
        return None
    total = _total(plate)
    if total <= 0:
        return None
    if found is not None:
        count = sum(p.quantity for p in found.parts)
        close = abs(found.total_grams - total) <= total * WEIGHT_CHECK
        if count == plate.object_count and close:
            parts = found.parts
    return ArchiveParts(plate.objects, plate.object_count, total, parts)
