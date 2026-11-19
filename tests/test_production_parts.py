"""The parts mask of a production (issue #215): lines of parts with quantity and waste, merging
and splitting, the waste quote and the number of good parts. Pure calculation, invented data.
The test case numbers refer to the comment on the issue.
"""

from decimal import Decimal

import pytest

from app.archive_parts import ArchiveParts
from app.plate_parts import PlatePart
from app.production_parts import (
    PartLine,
    PartsError,
    detect,
    finished_articles,
    merge,
    parse_detected,
    quote,
    same_article,
    split,
    stored_detected,
    totals,
)

D = Decimal


def line(name: str, qty: int, grams: str | None, waste: int = 0) -> PartLine:
    return PartLine(name, qty, D(grams) if grams else None, waste)


def test_1_same_parts_fifty_with_forty_waste() -> None:
    lines = [line("Clip", 50, "2", waste=40)]
    assert lines[0].good == 10
    assert quote(lines) == (D("0.8"), False)
    assert totals([lines]) == (10, 40)


def test_2_lantern_quote_by_weight() -> None:
    weights = ["5", "9", "14", "21", "33"]
    lines = [line(f"Teil {n}", 1, w) for n, w in enumerate(weights)]
    lines[3] = PartLine("Teil 3", 1, D("21"), 1)
    value, by_pieces = quote(lines)
    assert by_pieces is False
    assert (value * 100).quantize(D("0.1")) == D("25.6")  # 21 / 82
    # the 21 g part is missing: no finished lantern
    assert finished_articles([lines]) == 0


def test_3_modes_same_and_different_from_the_detected_lines() -> None:
    detected = [line("Body1", 1, "7.69"), line("Body3", 1, "7.53")]
    one = same_article(detected)
    assert len(one) == 1 and one[0].qty == 2
    assert one[0].grams == D("7.61")  # weighted by quantity
    assert one[0].waste == 0


def test_4_merge_and_split() -> None:
    lines = [line("Body1", 1, "7.69", 1), line("Body3", 1, "7.53"), line("Rim", 2, "3")]
    merged = merge(lines, [0, 1])
    assert [(x.name, x.qty, x.waste) for x in merged] == [("Body1", 2, 1), ("Rim", 2, 0)]
    assert merged[0].grams == D("7.61")
    parts = split(merged, 0)
    assert [(x.name, x.qty, x.waste) for x in parts] == [
        ("Body1", 1, 1),
        ("Body1", 1, 0),
        ("Rim", 2, 0),
    ]
    # fewer than two or a wrong index changes nothing
    assert merge(lines, [0]) == lines and merge(lines, [0, 9]) == lines
    assert split(lines, 0) == lines and split(lines, 9) == lines


def test_5_waste_and_quantity_are_checked() -> None:
    assert PartLine("A", 3, None, 3).good == 0
    for qty, waste in ((0, 0), (-1, 0), (2, 3), (1, -1)):
        with pytest.raises(PartsError):
            PartLine("A", qty, None, waste).check()
    PartLine("A", 3, D("1.5"), 3).check()
    with pytest.raises(PartsError):
        PartLine("A", 3, D("-1"), 0).check()


def test_8_several_plates_the_replacement_part_counts() -> None:
    plate1 = [line(f"T{n}", 1, "10") for n in range(5)]
    plate1[2] = PartLine("T2", 1, D("10"), 1)  # defective, 0 good
    plate2 = [line("t2", 1, "10")]  # printed again on a second plate (case does not matter)
    assert finished_articles([plate1]) == 0
    assert finished_articles([plate1, plate2]) == 1
    assert totals([plate1, plate2]) == (5, 1)


def test_8b_two_lanterns_on_one_plate() -> None:
    lines = [line(f"T{n}", 2, "10") for n in range(5)]
    lines[1] = PartLine("T1", 2, D("10"), 1)
    assert finished_articles([lines]) == 1


def test_9_without_weights_the_quote_is_by_pieces() -> None:
    lines = [line("A", 3, None, 1), line("B", 1, "5")]
    value, by_pieces = quote(lines)
    assert by_pieces is True
    assert value == D("0.25")  # 1 of 4 pieces


def test_quote_of_nothing_is_none() -> None:
    assert quote([]) is None
    assert quote([line("A", 0, None)]) is None
    assert quote([line("A", 2, "3")]) == (D("0"), False)


def test_detect_from_the_archive_parts() -> None:
    with_weights = ArchiveParts(
        ["Body1", "Body3"],
        2,
        D("15.23"),
        [PlatePart("Body1", 1, D("7.69"), {}), PlatePart("Body3", 1, D("7.53"), {})],
    )
    lines, mode = detect(with_weights)
    assert [(x.name, x.qty, x.grams) for x in lines] == [
        ("Body1", 1, D("7.69")),
        ("Body3", 1, D("7.53")),
    ]
    assert mode == "different"
    # no weights: the names of Bambuddy; the same name twice is one line with quantity 2
    no_weights = ArchiveParts(["Clip", "Clip", "Cap"], 3, D("5"), None)
    lines, mode = detect(no_weights)
    assert [(x.name, x.qty, x.grams) for x in lines] == [("Clip", 2, None), ("Cap", 1, None)]
    assert mode == "different"
    # one part with quantity > 1, or a single object: same article
    ten = ArchiveParts(["Clip"], 1, D("20"), [PlatePart("Clip", 10, D("2"), {})])
    assert detect(ten)[1] == "same" and detect(ten)[0][0].qty == 10
    single = ArchiveParts(["Vase"], 1, D("10"), [PlatePart("Vase", 1, D("10"), {})])
    assert detect(single)[1] == "same"
    assert detect(None) == ([], "")


def test_detected_roundtrip_is_validated() -> None:
    lines = [line("Body1", 1, "7.69"), line("Rim", 2, None)]
    assert parse_detected(stored_detected(lines)) == lines
    for bad in ("", "x", "[1]", '[["a","1"]]', '[["a","x","1"]]', '[["a","0",""]]', "[" * 500):
        assert parse_detected(bad) == []
    big = stored_detected([line(f"N{n}", 1, None) for n in range(300)])
    assert parse_detected(big) == []
