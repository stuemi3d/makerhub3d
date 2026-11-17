"""Reading sliced files (issue #160): print time and grams per material from a sliced 3MF
(Bambu Studio / Orca, "plate sliced") or from a G-code file.

Pure reading, no page and no database. The caller passes the bytes of the file; nothing is
written to disk and no name inside an archive is ever used as a path. Safety limits:
file size, number of archive entries, bytes read per entry (the declared sizes of an archive
are not trusted), line length. XML with a DOCTYPE or entities is refused.

Time per material comes from the G-code: the printer keeps a "minutes left" marker (M73) and a
filament change marker (M620). Without them the total time is split by grams and the plate
is marked as estimated. Mistakes in a file raise `SliceFileError` with a short `code` for the
page to translate; they never raise anything else.
"""

import io
import json
import re
import zipfile
import zlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

MAX_FILE = 50 * 1024 * 1024
MAX_ENTRIES = 200
MAX_PLATES = 50
MAX_XML = 2 * 1024 * 1024
MAX_SETTINGS = 4 * 1024 * 1024
MAX_GCODE = 200 * 1024 * 1024
MAX_LINE = 1024 * 1024
MAX_GRAMS = Decimal(1_000_000)
MAX_SECONDS = 10 * 365 * 24 * 3600

ZERO = Decimal(0)
_FILAMENT_CHANGE = re.compile(rb"M620 S(\d+)A")
_PROGRESS = re.compile(rb"M73 P\d+ R(\d+)")
_DURATION = re.compile(r"(?:\s*\d+\s*[dhms])+\s*")
_NOZZLE = re.compile(r"\s+\d+(?:\.\d+)?\s*nozzle$", re.IGNORECASE)
_BRAND = re.compile(r"^bambu\s*lab\s+", re.IGNORECASE)


class SliceFileError(ValueError):
    """The file cannot be used. `code` is one of: not_a_slice_file, not_sliced, unreadable,
    too_large, missing_values, values_invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SliceFilament:
    material: str | None
    color: str | None
    grams: Decimal
    seconds: int  # share of the print time of the plate


@dataclass(frozen=True)
class SlicePlate:
    index: int
    seconds: int  # total time, including heating and filament changes
    filaments: list[SliceFilament]
    times_estimated: bool  # True: the time per material is split by grams


@dataclass(frozen=True)
class SliceFile:
    plates: list[SlicePlate]
    printer_name: str | None
    printer_model: str | None


@dataclass
class _Gcode:
    seconds: int | None = None
    grams: list[Decimal] | None = None
    materials: list[str] | None = None
    colors: list[str] | None = None
    printer_model: str | None = None
    printer_settings_id: str | None = None
    # Events in file order: ("left", minutes) and ("filament", index)
    events: list[tuple[str, int]] | None = None
    known_keys: int = 0
    bytes_read: int = 0


def parse_duration(text: str) -> int:
    """'1d 2h 3m 4s' to seconds."""
    if not _DURATION.fullmatch(text or ""):
        raise SliceFileError("values_invalid")
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    total = sum(int(n) * units[u] for n, u in re.findall(r"(\d+)\s*([dhms])", text))
    if total > MAX_SECONDS:
        raise SliceFileError("values_invalid")
    return total


def _grams(text: str) -> Decimal:
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise SliceFileError("values_invalid") from None
    if not value.is_finite() or value < 0 or value >= MAX_GRAMS:
        raise SliceFileError("values_invalid")
    return value


def _seconds(text: str) -> int:
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise SliceFileError("values_invalid") from None
    if not value.is_finite() or value < 0 or value > MAX_SECONDS:
        raise SliceFileError("values_invalid")
    return int(value)


def _read_limited(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    try:
        with archive.open(name) as stream:
            data = stream.read(limit + 1)
    except (zipfile.BadZipFile, RuntimeError, zlib.error, EOFError, NotImplementedError):
        raise SliceFileError("unreadable") from None
    if len(data) > limit:
        raise SliceFileError("too_large")
    return data


def _printer(model: str | None, settings_id: str | None) -> tuple[str | None, str | None]:
    """The printer name as written in the file and the short model ("Bambu Lab X1E" -> "X1E")."""
    name = (model or "").strip() or _NOZZLE.sub("", (settings_id or "").strip())
    if not name:
        return None, None
    short = _BRAND.sub("", name).strip()
    return name, short or None


def _parse_gcode(stream: io.BufferedIOBase, limit: int) -> _Gcode:
    """One pass over the lines. Only comment lines of interest and the M73 / M620 markers are
    looked at; the rest is skipped without being decoded."""
    info = _Gcode(events=[])
    read = 0
    while True:
        line = stream.readline(MAX_LINE + 1)
        if not line:
            break
        read += len(line)
        if len(line) > MAX_LINE or read > limit:
            raise SliceFileError("too_large")
        if line.startswith(b"M73 "):
            match = _PROGRESS.match(line)
            if match:
                info.events.append(("left", int(match.group(1))))
        elif line.startswith(b"M620 "):
            match = _FILAMENT_CHANGE.match(line)
            if match and int(match.group(1)) < 255:
                info.events.append(("filament", int(match.group(1))))
        elif line.startswith(b";"):
            _comment(info, line.decode("utf-8", "replace").strip())
    info.bytes_read = read
    return info


def _comment(info: _Gcode, line: str) -> None:
    text = line.lstrip("; ").strip()
    if "total estimated time:" in text and info.seconds is None:
        info.seconds = parse_duration(text.split("total estimated time:")[1].strip())
        info.known_keys += 1
    elif text.startswith("estimated printing time (normal mode) =") and info.seconds is None:
        info.seconds = parse_duration(text.split("=", 1)[1].strip())
        info.known_keys += 1
    elif text.startswith("total filament weight [g] :") and info.grams is None:
        info.grams = [_grams(part) for part in text.split(":", 1)[1].split(",")]
        info.known_keys += 1
    elif text.startswith("filament used [g] =") and info.grams is None:
        info.grams = [_grams(part) for part in text.split("=", 1)[1].split(",")]
        info.known_keys += 1
    elif text.startswith("filament_type =") and info.materials is None:
        info.materials = [part.strip() for part in text.split("=", 1)[1].split(";")]
        info.known_keys += 1
    elif text.startswith("filament_colour =") and info.colors is None:
        info.colors = [part.strip() for part in text.split("=", 1)[1].split(";")]
        info.known_keys += 1
    elif text.startswith("printer_model =") and info.printer_model is None:
        info.printer_model = text.split("=", 1)[1].strip()
    elif text.startswith("printer_settings_id =") and info.printer_settings_id is None:
        info.printer_settings_id = text.split("=", 1)[1].strip()


def _minutes_per_filament(events: list[tuple[str, int]]) -> dict[int, int]:
    """Minutes of print time per filament index, from the "minutes left" markers between the
    filament changes. Empty when the file has no usable markers."""
    minutes: dict[int, int] = {}
    current: int | None = None
    start: int | None = None
    left: int | None = None
    for kind, value in events:
        if kind == "left":
            left = value
            if start is None:
                start = value
        else:
            if current is not None and start is not None and left is not None:
                minutes[current] = minutes.get(current, 0) + max(start - left, 0)
                start = left
            current = value
    if current is not None and start is not None and left is not None:
        minutes[current] = minutes.get(current, 0) + max(start - left, 0)
    return minutes


def _split_time(
    total: int, grams: list[Decimal], events: list[tuple[str, int]], indices: list[int]
) -> tuple[list[int], bool]:
    """The seconds per filament (`indices` are their 0-based numbers in the printer's filament
    change markers) and whether they are only estimated (split by grams). Filaments without
    grams (unused slots) need no markers."""
    if len(grams) == 1:
        return [total], False
    minutes = _minutes_per_filament(events)
    used = [index for index, gram in zip(indices, grams, strict=True) if gram > 0]
    if minutes and all(index in minutes for index in used) and sum(minutes.values()) > 0:
        weights = [Decimal(minutes.get(index, 0)) for index in indices]
        estimated = False
    else:
        weights = list(grams)
        estimated = True
    return _distribute(total, weights), estimated


def _distribute(total: int, weights: list[Decimal]) -> list[int]:
    """Whole seconds in proportion to the weights; the last one takes the remainder."""
    weight_sum = sum(weights, ZERO)
    if weight_sum <= 0:
        weights = [Decimal(1)] * len(weights)
        weight_sum = Decimal(len(weights))
    parts = [int(Decimal(total) * weight / weight_sum) for weight in weights[:-1]]
    parts.append(total - sum(parts))
    return parts


def _from_gcode(data: bytes) -> SliceFile:
    if b"\0" in data[:8192]:
        raise SliceFileError("not_a_slice_file")
    info = _parse_gcode(io.BytesIO(data), MAX_GCODE)
    if info.known_keys == 0:
        raise SliceFileError("not_a_slice_file")
    if info.seconds is None or not info.grams:
        raise SliceFileError("missing_values")
    seconds, estimated = _split_time(
        info.seconds, info.grams, info.events or [], list(range(len(info.grams)))
    )
    filaments = [
        SliceFilament(
            material=_item(info.materials, n),
            color=_item(info.colors, n),
            grams=grams,
            seconds=seconds[n],
        )
        for n, grams in enumerate(info.grams)
    ]
    plate = SlicePlate(1, info.seconds, filaments, estimated)
    name, model = _printer(info.printer_model, info.printer_settings_id)
    return SliceFile([plate], name, model)


def _item(values: list[str] | None, index: int) -> str | None:
    if values is None or index >= len(values):
        return None
    return values[index] or None


def _xml(data: bytes) -> ElementTree.Element:
    # UTF-16 and the like contain NUL bytes and would slip past the check below
    if b"\0" in data:
        raise SliceFileError("unreadable")
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise SliceFileError("unreadable")
    try:
        # DOCTYPE and entities are refused above, so nothing is expanded or loaded
        return ElementTree.fromstring(data)  # noqa: S314
    except ElementTree.ParseError:
        raise SliceFileError("unreadable") from None


def _from_3mf(data: bytes) -> SliceFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = set(archive.namelist())
    except (zipfile.BadZipFile, EOFError, ValueError):
        raise SliceFileError("unreadable") from None
    if len(names) > MAX_ENTRIES:
        raise SliceFileError("unreadable")
    if "Metadata/slice_info.config" not in names:
        raise SliceFileError("not_a_slice_file")
    root = _xml(_read_limited(archive, "Metadata/slice_info.config", MAX_XML))
    plate_elements = root.findall("plate")
    if not plate_elements:
        raise SliceFileError("not_sliced")

    if len(plate_elements) > MAX_PLATES:
        raise SliceFileError("unreadable")
    # Every G-code is read once, and all together within one budget
    budget = [MAX_GCODE]
    cache: dict[str, list[tuple[str, int]]] = {}
    plates = [
        _plate(archive, names, element, position, cache, budget)
        for position, element in enumerate(plate_elements, start=1)
    ]
    name, model = _printer_of_3mf(archive, names)
    return SliceFile(plates, name, model)


def _gcode_events(
    archive: zipfile.ZipFile,
    name: str,
    cache: dict[str, list[tuple[str, int]]],
    budget: list[int],
) -> list[tuple[str, int]]:
    if name not in cache:
        try:
            with archive.open(name) as stream:
                info = _parse_gcode(stream, budget[0])
                budget[0] -= info.bytes_read
        except (zipfile.BadZipFile, RuntimeError, zlib.error, EOFError, NotImplementedError):
            raise SliceFileError("unreadable") from None
        cache[name] = info.events or []
    return cache[name]


def _plate(
    archive: zipfile.ZipFile,
    names: set[str],
    element: ElementTree.Element,
    position: int,
    cache: dict[str, list[tuple[str, int]]],
    budget: list[int],
) -> SlicePlate:
    meta = {m.get("key"): m.get("value") or "" for m in element.findall("metadata")}
    try:
        index = int(meta.get("index", position))
    except ValueError:
        raise SliceFileError("values_invalid") from None
    if "prediction" not in meta or not element.findall("filament"):
        raise SliceFileError("missing_values")
    total = _seconds(meta["prediction"])
    items = element.findall("filament")
    grams = [_grams(item.get("used_g", "")) for item in items]
    events: list[tuple[str, int]] = []
    gcode_name = f"Metadata/plate_{index}.gcode"
    if gcode_name in names:
        events = _gcode_events(archive, gcode_name, cache, budget)
    try:
        indices = [int(item.get("id", "")) - 1 for item in items]
    except ValueError:
        raise SliceFileError("values_invalid") from None
    seconds, estimated = _split_time(total, grams, events, indices)
    filaments = [
        SliceFilament(
            material=(item.get("type") or "").strip() or None,
            color=(item.get("color") or "").strip() or None,
            grams=gram,
            seconds=part,
        )
        for item, gram, part in zip(items, grams, seconds, strict=True)
    ]
    return SlicePlate(index, total, filaments, estimated)


def _printer_of_3mf(archive: zipfile.ZipFile, names: set[str]) -> tuple[str | None, str | None]:
    if "Metadata/project_settings.config" not in names:
        return None, None
    raw = _read_limited(archive, "Metadata/project_settings.config", MAX_SETTINGS)
    try:
        settings = json.loads(raw)
    except (ValueError, RecursionError):
        return None, None
    if not isinstance(settings, dict):
        return None, None
    model = settings.get("printer_model")
    settings_id = settings.get("printer_settings_id")
    return _printer(
        model if isinstance(model, str) else None,
        settings_id if isinstance(settings_id, str) else None,
    )


def read_slice_file(data: bytes, max_bytes: int = MAX_FILE) -> SliceFile:
    """Read a sliced 3MF or a G-code file. The type is taken from the content, not the name."""
    if len(data) > max_bytes:
        raise SliceFileError("too_large")
    try:
        if data[:4] == b"PK\x03\x04":
            return _from_3mf(data)
        return _from_gcode(data)
    except SliceFileError:
        raise
    except Exception:  # whatever a damaged file does, the caller gets exactly one error type
        raise SliceFileError("unreadable") from None
