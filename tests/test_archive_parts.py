"""Parts of an archive entry, natively from Bambuddy first (issue #214). The answers of Bambuddy
are recorded and cleaned (invented names); the files are invented. Nothing talks to a real API.
"""

import json
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

from app.archive_parts import WEIGHT_CHECK, archive_parts
from app.bambuddy import BambuddyClient, BambuddyError
from tests.test_plate_parts import sliced

D = Decimal
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bambuddy"


def client(plates: str, file: bytes | int | None = None, requests: list | None = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path.endswith("/plates"):
            return httpx2.Response(200, text=(FIXTURES / plates).read_text(encoding="utf-8"))
        if isinstance(file, int):
            return httpx2.Response(file)
        return httpx2.Response(200, content=file or b"")

    return BambuddyClient(
        "http://bambuddy.example.com", "k", transport=httpx2.MockTransport(handler)
    )


def test_plates_are_read_from_bambuddy() -> None:
    (plate,) = client("archive_plates_multi.json").archive_plates(261)
    assert (plate.index, plate.objects, plate.object_count) == (1, ["Cube", "Cylinder"], 2)
    assert plate.filament_grams == D("44")
    assert [(f.color, f.grams) for f in plate.filaments] == [("#AA0000", D("44"))]


def test_single_object_needs_no_file() -> None:
    requests: list = []
    result = archive_parts(client("archive_plates_single.json", None, requests), 260)
    assert result is not None
    (part,) = result.parts
    assert (part.name, part.quantity, part.grams_each) == ("Vase", 1, D("10"))
    assert part.grams_by_color == {"#AA0000": D("6.5"), "#0000BB": D("3.5")}
    assert [r.url.path for r in requests] == ["/api/v1/archives/260/plates"]  # no download


def test_several_objects_weights_from_file_when_they_match() -> None:
    data = sliced([("Cube", 10.0, 0), ("Cylinder", 30.0, 0)], shared=4.0)
    result = archive_parts(client("archive_plates_multi.json", data), 261)
    assert result is not None
    assert result.object_count == 2 and result.total_grams == D("44")
    assert [(p.name, p.quantity) for p in result.parts] == [("Cube", 1), ("Cylinder", 1)]
    assert abs(result.parts[1].grams_each - D("33")) < D("0.02")


def test_file_that_does_not_match_bambuddy_is_not_used() -> None:
    # a different slicer format or an update: the sum is far from Bambuddy's 44 g
    wrong_weight = sliced([("Cube", 10.0, 0), ("Cylinder", 20.0, 0)])
    wrong_count = sliced([("Cube", 10.0, 0), ("Cylinder", 30.0, 0), ("Cone", 4.0, 0)], shared=0)
    for data in (wrong_weight, wrong_count, b"damaged", b""):
        result = archive_parts(client("archive_plates_multi.json", data), 261)
        # Bambuddy's own figures stay; only the weights per part are missing
        assert result is not None
        assert result.parts is None
        assert (result.names, result.object_count, result.total_grams) == (
            ["Cube", "Cylinder"],
            2,
            D("44"),
        )
    assert D("0") < WEIGHT_CHECK < D("0.05")


def test_file_download_error_keeps_the_native_figures() -> None:
    result = archive_parts(client("archive_plates_multi.json", 404), 261)
    assert result is not None and result.parts is None and result.object_count == 2


def test_bambuddy_errors_on_the_plates_are_raised() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403)

    broken = BambuddyClient(
        "http://bambuddy.example.com", "k", transport=httpx2.MockTransport(handler)
    )
    with pytest.raises(BambuddyError) as error:
        archive_parts(broken, 1)
    assert error.value.code == "forbidden"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(plates="no list"),
        lambda d: d["plates"][0].update(objects="Cube"),
        lambda d: d["plates"][0].update(object_count="2"),
        lambda d: d["plates"][0].update(filaments=[{"color": "#fff", "used_grams": "x"}]),
    ],
)
def test_odd_plate_answers_give_bad_response(mutate) -> None:
    data = json.loads((FIXTURES / "archive_plates_multi.json").read_text(encoding="utf-8"))
    mutate(data)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=data)

    odd = BambuddyClient(
        "http://bambuddy.example.com", "k", transport=httpx2.MockTransport(handler)
    )
    with pytest.raises(BambuddyError) as error:
        odd.archive_plates(1)
    assert error.value.code == "bad_response"


def test_entry_without_a_file_gives_no_result() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"detail": "Archive file not found"})

    missing = BambuddyClient(
        "http://bambuddy.example.com", "k", transport=httpx2.MockTransport(handler)
    )
    assert archive_parts(missing, 277) is None
