"""Parts of a plate from a sliced .gcode.3mf (issue #214): which parts are on the plate, how many
of each, and how heavy one piece is.

Names come from the 3MF (Metadata/model_settings.config), the filament per part from the
object markers in the G-code ("start/stop printing object, unique label id"). Every move with
extrusion counts (G0 to G3, so arcs too); the length of filament is turned into grams with the
diameter and density written in the file. Filament printed outside of any object marker (flush,
wipe tower) is shared out in proportion to the weight of the parts.

Pure reading, no page and no database: the caller passes the bytes, nothing is written to disk
and no name inside the archive is ever used as a path. The result is an attempt: whatever is
wrong with the file, `parts_from_file` returns None (never an error with details).
"""

import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

from app.slice_reader import (
    MAX_ENTRIES,
    MAX_FILE,
    MAX_GCODE,
    MAX_LINE,
    MAX_XML,
)

# Parts with different names count as the same part when their weights differ by at most this
# share (3 %). Set from invented test files; to be checked with real files.
WEIGHT_TOLERANCE = Decimal("0.03")
MAX_NAME = 80
MAX_PARTS = 500
MAX_FILAMENTS = 32

ZERO = Decimal(0)
_MOVE = re.compile(rb"G[0-3](?=\s)")
_NUMBER = rb"(-?(?:\d+(?:\.\d*)?|\.\d+))"  # also ".04" and "-.8"
_E_WORD = re.compile(rb"\sE" + _NUMBER + rb"(?=\s|$)")
_RESET = re.compile(rb"G92\s.*?\bE" + _NUMBER)
_START = re.compile(rb"; start printing object, unique label id: (\S+)")
_STOP = re.compile(rb"; stop printing object, unique label id: (\S+)")
_TOOL = re.compile(rb"(?:M620 S|T)(\d+)A?\s*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_GCODE_NAME = re.compile(r"Metadata/plate_(\d+)\.gcode")


class _Unusable(Exception):
    pass


@dataclass(frozen=True)
class PlatePart:
    name: str
    quantity: int
    grams_each: Decimal  # one piece, with its share of flush and tower
    grams_by_color: dict[str, Decimal]  # one piece, by colour code ("" when unknown)


@dataclass(frozen=True)
class PlateParts:
    parts: list[PlatePart]
    total_grams: Decimal  # everything extruded on the plate


@dataclass
class _Gcode:
    diameters: list[Decimal] = field(default_factory=list)
    densities: list[Decimal] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    # filament index -> mm of filament per object label; label None = outside of any object
    mm: dict[tuple[str | None, int], float] = field(default_factory=dict)


def _decimal_list(text: str, separator: str) -> list[Decimal]:
    try:
        values = [Decimal(part.strip()) for part in text.split(separator) if part.strip()]
    except InvalidOperation:
        raise _Unusable from None
    if len(values) > MAX_FILAMENTS or any(not v.is_finite() or v <= 0 for v in values):
        raise _Unusable
    return values


def _header(info: _Gcode, line: bytes) -> None:
    text = line.decode("utf-8", "replace").lstrip("; ").strip()
    if text.startswith("filament_diameter") and not info.diameters:
        info.diameters = _decimal_list(text.split(":", 1)[-1].split("=", 1)[-1], ",")
    elif text.startswith("filament_density") and not info.densities:
        info.densities = _decimal_list(text.split(":", 1)[-1].split("=", 1)[-1], ",")
    elif text.startswith("filament_colour") and not info.colors:
        raw = text.split("=", 1)[-1]
        info.colors = [part.strip() for part in raw.split(";")][:MAX_FILAMENTS]


def _read_gcode(stream: io.BufferedIOBase) -> _Gcode:
    info = _Gcode()
    read = 0
    absolute = False
    last_e = 0.0
    label: str | None = None
    filament = 0
    while True:
        line = stream.readline(MAX_LINE + 1)
        if not line:
            break
        read += len(line)
        if len(line) > MAX_LINE or read > MAX_GCODE:
            raise _Unusable
        if line[:1] == b";":
            if line.startswith(b"; start printing object"):
                match = _START.match(line)
                label = match.group(1).decode("ascii", "replace") if match else None
            elif line.startswith(b"; stop printing object"):
                label = None
            elif b"filament_" in line[:20]:
                _header(info, line)
            continue
        if line.startswith(b"M82"):
            absolute = True
        elif line.startswith(b"M83"):
            absolute = False
        elif line.startswith(b"G92"):
            match = _RESET.match(line.split(b";", 1)[0])
            if match:
                last_e = float(match.group(1))
        elif line.startswith((b"M620 S", b"T")):
            match = _TOOL.match(line.rstrip())
            if match and int(match.group(1)) < MAX_FILAMENTS:
                filament = int(match.group(1))
        elif _MOVE.match(line):
            match = _E_WORD.search(line.split(b";", 1)[0].rstrip() + b" ")
            if not match:
                continue
            value = float(match.group(1))
            if absolute:
                delta, last_e = value - last_e, value
            else:
                delta = value
            if not math.isfinite(delta):
                raise _Unusable
            key = (label, filament)
            if key not in info.mm and len(info.mm) >= MAX_PARTS * MAX_FILAMENTS:
                raise _Unusable
            info.mm[key] = info.mm.get(key, 0.0) + delta
    return info


def _clean_xml(data: bytes) -> ElementTree.Element:
    lowered = data.lower()
    if b"\0" in data or b"<!doctype" in lowered or b"<!entity" in lowered:
        raise _Unusable
    try:
        # DOCTYPE and entities are refused above, so nothing is expanded or loaded
        return ElementTree.fromstring(data)  # noqa: S314
    except ElementTree.ParseError:
        raise _Unusable from None


def _meta(element: ElementTree.Element) -> dict[str, str]:
    return {m.get("key") or "": m.get("value") or "" for m in element.findall("metadata")}


def _clean_name(text: str) -> str:
    return _CONTROL.sub(" ", text).strip()[:MAX_NAME] or "?"


def _names_by_label(root: ElementTree.Element, plate_index: int) -> dict[str, str]:
    """Label id (identify_id) -> name of the object, for the objects on the plate."""
    objects = {
        element.get("id") or "": _clean_name(_meta(element).get("name", ""))
        for element in root.findall("object")
    }
    plates = root.findall("plate")
    chosen = [p for p in plates if _meta(p).get("plater_id") == str(plate_index)] or plates[:1]
    names: dict[str, str] = {}
    for plate in chosen:
        for instance in plate.findall("model_instance"):
            meta = _meta(instance)
            label = meta.get("identify_id")
            name = objects.get(meta.get("object_id", ""))
            if label and name:
                names[label] = name
    return names


def _open_gcode(archive: zipfile.ZipFile, name: str) -> _Gcode:
    try:
        with archive.open(name) as stream:
            return _read_gcode(stream)
    except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError, OSError):
        raise _Unusable from None
    except Exception as error:  # zlib.error and the like
        if isinstance(error, _Unusable):
            raise
        raise _Unusable from None


def _grams(mm: float, diameter: Decimal, density: Decimal) -> Decimal:
    area = Decimal(str(math.pi)) * (diameter / 2) ** 2  # mm2
    return Decimal(str(mm)) * area / 1000 * density


def _pick(values: list[Decimal], index: int) -> Decimal:
    if not values:
        raise _Unusable
    return values[index] if index < len(values) else values[0]


def _compute(data: bytes) -> PlateParts:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = archive.namelist()
    except (zipfile.BadZipFile, EOFError, ValueError):
        raise _Unusable from None
    if len(names) > MAX_ENTRIES:
        raise _Unusable
    plates = sorted(int(m.group(1)) for n in names if (m := _GCODE_NAME.fullmatch(n)))
    if not plates or "Metadata/model_settings.config" not in names:
        raise _Unusable
    plate_index = plates[0]
    info_entry = archive.getinfo("Metadata/model_settings.config")
    if info_entry.file_size > MAX_XML:
        raise _Unusable
    with archive.open(info_entry) as stream:
        raw = stream.read(MAX_XML + 1)
    if len(raw) > MAX_XML:
        raise _Unusable
    labels = _names_by_label(_clean_xml(raw), plate_index)
    info = _open_gcode(archive, f"Metadata/plate_{plate_index}.gcode")
    used = {label for (label, _), mm in info.mm.items() if label is not None and mm > 0}
    if not labels or not used & labels.keys():
        raise _Unusable
    if len(labels) > MAX_PARTS:
        raise _Unusable

    # grams per object label and filament, and the shared rest
    own: dict[str, dict[int, Decimal]] = {}
    shared: dict[int, Decimal] = {}
    for (label, filament), mm in info.mm.items():
        grams = _grams(mm, _pick(info.diameters, filament), _pick(info.densities, filament))
        if label is None or label not in labels:
            shared[filament] = max(shared.get(filament, ZERO) + grams, ZERO)
        else:
            own.setdefault(label, {})[filament] = grams
    own_total = sum((g for per in own.values() for g in per.values()), ZERO)
    if own_total <= 0:
        raise _Unusable
    total = own_total + sum(shared.values(), ZERO)

    instances: list[tuple[str, Decimal, dict[str, Decimal]]] = []
    for label, per_filament in own.items():
        weight = sum(per_filament.values(), ZERO)
        by_color: dict[str, Decimal] = {}
        for filament in set(per_filament) | set(shared):
            part = per_filament.get(filament, ZERO)
            part += shared.get(filament, ZERO) * weight / own_total
            color = info.colors[filament] if filament < len(info.colors) else ""
            by_color[color] = by_color.get(color, ZERO) + part
        instances.append((labels[label], sum(by_color.values(), ZERO), by_color))
    return PlateParts(_group(instances), total)


def _group(instances: list[tuple[str, Decimal, dict[str, Decimal]]]) -> list[PlatePart]:
    # step 1: the same name; step 2: groups whose weight is within the tolerance
    groups: list[list[tuple[str, Decimal, dict[str, Decimal]]]] = []
    by_name: dict[str, list] = {}
    for item in sorted(instances, key=lambda i: i[0]):
        if item[0] not in by_name:
            by_name[item[0]] = []
            groups.append(by_name[item[0]])
        by_name[item[0]].append(item)
    merged: list[list[tuple[str, Decimal, dict[str, Decimal]]]] = []
    for group in groups:
        each = sum(i[1] for i in group) / len(group)
        for target in merged:
            reference = sum(i[1] for i in target) / len(target)
            if abs(each - reference) <= reference * WEIGHT_TOLERANCE:
                target.extend(group)
                break
        else:
            merged.append(list(group))
    parts = []
    for group in merged:
        count = len(group)
        colors: dict[str, Decimal] = {}
        for _, _, by_color in group:
            for color, grams in by_color.items():
                colors[color] = colors.get(color, ZERO) + grams / count
        grams_each = sum((i[1] for i in group), ZERO) / count
        # shortest name of the group stands for it (the original, not "Name copy")
        name = min((i[0] for i in group), key=lambda n: (len(n), n))
        parts.append(PlatePart(name, count, grams_each, colors))
    return parts


def parts_from_file(data: bytes, max_bytes: int = MAX_FILE) -> PlateParts | None:
    """The parts of the plate in a sliced .gcode.3mf, or None when the file cannot be used
    (not a 3MF, no G-code, no object markers, damaged, too large)."""
    if len(data) > max_bytes or data[:4] != b"PK\x03\x04":
        return None
    try:
        return _compute(data)
    except Exception:  # whatever a damaged file does: no result, no details
        return None
