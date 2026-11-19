"""Parts of a plate from a sliced .gcode.3mf (issue #214). All files here are invented and built
in the test: a small generator writes G-code with object markers, so no personal data is
involved. The test case numbers refer to the comments on the issue.
"""

import io
import math
import zipfile
from decimal import Decimal

from app import plate_parts
from app.plate_parts import parts_from_file

D = Decimal
DIAMETER = 1.75
DENSITY = 1.25
AREA = math.pi * (DIAMETER / 2) ** 2  # mm2 of filament cross section


def mm_for_grams(grams: float) -> float:
    """Filament length in mm that weighs `grams`."""
    return grams / DENSITY * 1000 / AREA


def gcode(objects: list[tuple[str, float, int]], shared: float = 0.0, arcs: bool = False) -> bytes:
    """objects: (label id, grams, filament index). `shared` grams are printed outside of any
    object marker (flush / wipe tower), with filament 0."""
    lines = [
        "; HEADER_BLOCK_START",
        f"; filament_diameter: {DIAMETER},{DIAMETER}",
        f"; filament_density: {DENSITY},{DENSITY}",
        "; filament_colour = #FF0000;#0000FF",
        "M83",
        "G92 E0",
    ]
    for label, grams, filament in objects:
        lines.append(f"M620 S{filament}A")
        lines.append(f"; start printing object, unique label id: {label}")
        half = mm_for_grams(grams) / 2
        lines.append("G1 X10 Y10 E-0.8")  # retraction nets out with the unretract below
        lines.append("G1 X11 Y10 E0.8")
        lines.append(f"G1 X20 Y10 E{half:.5f}")
        if arcs:
            lines.append(f"G3 X30 Y20 I5 J5 E{half:.5f}")
        else:
            lines.append(f"G2 X30 Y20 I5 J5 E{half:.5f}")
        lines.append(f"; stop printing object, unique label id: {label}")
    if shared:
        lines.append("M620 S0A")
        lines.append(f"G1 X50 Y50 E{mm_for_grams(shared):.5f}")
    return ("\n".join(lines) + "\n").encode()


def model_settings(objects: list[tuple[str, str, str]]) -> bytes:
    """objects: (object id, name, label id). One instance each."""
    parts = []
    instances = []
    for object_id, name, label in objects:
        parts.append(f'<object id="{object_id}"><metadata key="name" value="{name}"/></object>')
        instances.append(
            "<model_instance>"
            f'<metadata key="object_id" value="{object_id}"/>'
            '<metadata key="instance_id" value="0"/>'
            f'<metadata key="identify_id" value="{label}"/>'
            "</model_instance>"
        )
    plate = '<plate><metadata key="plater_id" value="1"/>' + "".join(instances) + "</plate>"
    return f"<config>{''.join(parts)}{plate}</config>".encode()


def make_file(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def sliced(named: list[tuple[str, float, int]], shared: float = 0.0, arcs: bool = False) -> bytes:
    """named: (part name, grams, filament index); every entry is its own object instance."""
    objects = [(str(n + 2), name, str(100 + n)) for n, (name, _, _) in enumerate(named)]
    marks = [(str(100 + n), grams, filament) for n, (_, grams, filament) in enumerate(named)]
    return make_file(
        {
            "Metadata/model_settings.config": model_settings(objects),
            "Metadata/plate_1.gcode": gcode(marks, shared, arcs),
        }
    )


def test_1_two_different_parts_and_shared_share_adds_up() -> None:
    result = parts_from_file(sliced([("Cube", 10.0, 0), ("Cylinder", 30.0, 0)], shared=4.0))
    assert result is not None
    assert [p.name for p in result.parts] == ["Cube", "Cylinder"]
    assert [p.quantity for p in result.parts] == [1, 1]
    cube, cylinder = result.parts
    # the shared 4 g are split 1:3 by part weight
    assert abs(cube.grams_each - D("11")) < D("0.01")
    assert abs(cylinder.grams_each - D("33")) < D("0.01")
    total = sum(p.grams_each * p.quantity for p in result.parts)
    assert abs(total - D("44")) < D("0.02")
    assert abs(result.total_grams - D("44")) < D("0.02")


def test_2_ten_copies_are_one_row() -> None:
    result = parts_from_file(sliced([("Clip", 2.0, 0)] * 10))
    assert result is not None
    assert [(p.name, p.quantity) for p in result.parts] == [("Clip", 10)]
    assert abs(result.parts[0].grams_each - D("2")) < D("0.01")


def test_3_five_different_parts_and_one_copy() -> None:
    named = [
        ("Top", 5.0, 0),
        ("Roof", 9.0, 0),
        ("Wall A", 14.0, 0),
        ("Wall B", 21.0, 0),
        ("Base", 33.0, 0),
        ("Wall A", 14.0, 0),
    ]
    result = parts_from_file(sliced(named))
    assert result is not None
    assert len(result.parts) == 5
    by_name = {p.name: p.quantity for p in result.parts}
    assert by_name == {"Top": 1, "Roof": 1, "Wall A": 2, "Wall B": 1, "Base": 1}


def test_4_tolerance_and_names() -> None:
    # different names, weights within the tolerance: same part
    close = parts_from_file(sliced([("Leaf", 10.00, 0), ("Leaf copy", 10.15, 0)]))
    assert close is not None
    assert [p.quantity for p in close.parts] == [2]
    # same name: same part, even when imported with a small difference
    same_name = parts_from_file(sliced([("Leaf", 10.0, 0), ("Leaf", 10.1, 0)]))
    assert same_name is not None and [p.quantity for p in same_name.parts] == [2]
    # different names and clearly different weight: two parts
    apart = parts_from_file(sliced([("Leaf", 10.0, 0), ("Stem", 14.0, 0)]))
    assert apart is not None
    assert [p.quantity for p in apart.parts] == [1, 1]
    assert D("0.1") > plate_parts.WEIGHT_TOLERANCE


def test_5_arcs_are_counted() -> None:
    straight = parts_from_file(sliced([("Cylinder", 20.0, 0)]))
    arcs = parts_from_file(sliced([("Cylinder", 20.0, 0)], arcs=True))
    assert straight is not None and arcs is not None
    assert abs(arcs.parts[0].grams_each - D("20")) < D("0.02")
    assert abs(straight.parts[0].grams_each - D("20")) < D("0.02")
    # an arc-only part is not underestimated
    only_arc = gcode([("7", 8.0, 0)]).replace(b"G2 ", b"G3 ")
    data = make_file(
        {
            "Metadata/model_settings.config": model_settings([("2", "Ring", "7")]),
            "Metadata/plate_1.gcode": only_arc,
        }
    )
    result = parts_from_file(data)
    assert result is not None and abs(result.parts[0].grams_each - D("8")) < D("0.02")


def test_5b_absolute_extrusion_mode() -> None:
    lines = [
        f"; filament_diameter: {DIAMETER}",
        f"; filament_density: {DENSITY}",
        "M82",
        "G92 E0",
        "; start printing object, unique label id: 7",
        f"G1 X1 E{mm_for_grams(3.0):.5f}",
        "G92 E0",
        f"G1 X2 E{mm_for_grams(2.0):.5f}",
        "; stop printing object, unique label id: 7",
    ]
    data = make_file(
        {
            "Metadata/model_settings.config": model_settings([("2", "Bar", "7")]),
            "Metadata/plate_1.gcode": ("\n".join(lines) + "\n").encode(),
        }
    )
    result = parts_from_file(data)
    assert result is not None and abs(result.parts[0].grams_each - D("5")) < D("0.02")


def test_6_unusable_files_give_no_result() -> None:
    good = sliced([("Cube", 5.0, 0)])
    assert parts_from_file(good) is not None
    # no G-code
    no_gcode = make_file({"Metadata/model_settings.config": model_settings([("2", "A", "7")])})
    assert parts_from_file(no_gcode) is None
    # G-code without object markers
    plain = make_file(
        {
            "Metadata/model_settings.config": model_settings([("2", "A", "7")]),
            "Metadata/plate_1.gcode": b"; filament_density: 1.25\nG1 X1 E5\n",
        }
    )
    assert parts_from_file(plain) is None
    # no model settings: names unknown
    no_names = make_file({"Metadata/plate_1.gcode": gcode([("7", 5.0, 0)])})
    assert parts_from_file(no_names) is None
    # damaged, wrong type, empty, too large
    assert parts_from_file(b"PK\x03\x04 not a zip") is None
    assert parts_from_file(b"just text") is None
    assert parts_from_file(b"") is None
    assert parts_from_file(good, max_bytes=10) is None
    # a plain G-code file is not a 3MF
    assert parts_from_file(gcode([("7", 5.0, 0)])) is None
    # broken XML and entities
    bad_xml = make_file(
        {
            "Metadata/model_settings.config": b"<config><object",
            "Metadata/plate_1.gcode": gcode([("7", 5.0, 0)]),
        }
    )
    assert parts_from_file(bad_xml) is None
    entity = make_file(
        {
            "Metadata/model_settings.config": b'<!DOCTYPE x [<!ENTITY a "b">]><config/>',
            "Metadata/plate_1.gcode": gcode([("7", 5.0, 0)]),
        }
    )
    assert parts_from_file(entity) is None
    # bad numbers in the file never crash
    bad_number = make_file(
        {
            "Metadata/model_settings.config": model_settings([("2", "A", "7")]),
            "Metadata/plate_1.gcode": b"; filament_density: abc\n; filament_diameter: 1.75\n"
            b"; start printing object, unique label id: 7\nG1 X1 E1e999\n",
        }
    )
    assert parts_from_file(bad_number) is None


def test_7_names_never_become_paths(tmp_path, monkeypatch) -> None:
    evil = make_file(
        {
            "../../evil.txt": b"x",
            "Metadata/model_settings.config": model_settings([("2", "../../etc/passwd", "7")]),
            "Metadata/plate_1.gcode": gcode([("7", 5.0, 0)]),
        }
    )
    monkeypatch.chdir(tmp_path)
    before = sorted(tmp_path.iterdir())
    result = parts_from_file(evil)
    assert result is not None
    assert sorted(tmp_path.iterdir()) == before  # nothing is written anywhere
    assert not (tmp_path.parent / "evil.txt").exists()
    # the name is only text, cleaned of control characters and cut to a sane length
    assert "\n" not in result.parts[0].name
    long_name = make_file(
        {
            "Metadata/model_settings.config": model_settings([("2", "x" * 500, "7")]),
            "Metadata/plate_1.gcode": gcode([("7", 5.0, 0)]),
        }
    )
    named = parts_from_file(long_name)
    assert named is not None and len(named.parts[0].name) <= plate_parts.MAX_NAME


def test_grams_per_color() -> None:
    result = parts_from_file(sliced([("Body", 10.0, 0), ("Body", 10.0, 1)], shared=2.0))
    assert result is not None
    (part,) = result.parts
    assert part.quantity == 2
    assert set(part.grams_by_color) == {"#FF0000", "#0000FF"}
    # per piece: 11 g in total; the shared 2 g were printed with the red filament
    assert abs(sum(part.grams_by_color.values()) - D("11")) < D("0.02")
    assert abs(part.grams_by_color["#FF0000"] - D("6")) < D("0.02")
    assert abs(part.grams_by_color["#0000FF"] - D("5")) < D("0.02")


def test_numbers_without_leading_zero_and_comments() -> None:
    mm = mm_for_grams(4.0)
    lines = [
        f"; filament_diameter: {DIAMETER}",
        f"; filament_density: {DENSITY}",
        "M82",
        "G92 E.0",
        "; start printing object, unique label id: 7",
        f"G1 X1 E{mm:.5f} ; E999",
        "G1 X2 ; E500",
        "G92 E.0",
        f"G1 X3 E{mm:.5f}",
        "; stop printing object, unique label id: 7",
        "G1 E.5",
    ]
    data = make_file(
        {
            "Metadata/model_settings.config": model_settings([("2", "Bar", "7")]),
            "Metadata/plate_1.gcode": ("\n".join(lines) + "\n").encode(),
        }
    )
    result = parts_from_file(data)
    assert result is not None and abs(result.parts[0].grams_each - D("8")) < D("0.02")


def test_too_many_labels_give_no_result() -> None:
    lines = ["; filament_diameter: 1.75", "; filament_density: 1.25", "M83"]
    for n in range(plate_parts.MAX_PARTS * plate_parts.MAX_FILAMENTS + 5):
        lines += [f"; start printing object, unique label id: {n}", "G1 X1 E1"]
    data = make_file(
        {
            "Metadata/model_settings.config": model_settings([("2", "A", "1")]),
            "Metadata/plate_1.gcode": ("\n".join(lines) + "\n").encode(),
        }
    )
    assert parts_from_file(data) is None


def slice_info(index: int, objects: list[tuple[str, str, bool]]) -> bytes:
    """The form real files from Bambu Studio 2.x use: objects with label id and name."""
    items = "".join(
        f'<object identify_id="{label}" name="{name}" skipped="{str(skipped).lower()}" />'
        for label, name, skipped in objects
    )
    return (
        f'<config><plate><metadata key="index" value="{index}"/>{items}</plate></config>'
    ).encode()


def test_names_from_slice_info_and_other_plate_number() -> None:
    # real files keep the objects in slice_info.config and may have plate_2.gcode only
    data = make_file(
        {
            "Metadata/slice_info.config": slice_info(
                2, [("7", "Lid", False), ("8", "Lid", False), ("9", "Skipped", True)]
            ),
            "Metadata/plate_2.gcode": gcode([("7", 6.0, 0), ("8", 6.0, 0), ("9", 3.0, 0)]),
        }
    )
    result = parts_from_file(data)
    assert result is not None
    assert [(p.name, p.quantity) for p in result.parts] == [("Lid", 2)]
    # the skipped object was printed outside of a known part: it is shared out
    assert abs(result.total_grams - D("15")) < D("0.05")
