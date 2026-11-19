"""The parts mask in the production form (issue #215). All data is invented; Bambuddy answers
come from a mock (prints, plates, file). The test case numbers refer to the comment on the
issue (test cases 1-9).
"""

import html as htmllib
import re

import httpx2
import pytest

from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.machine import add_printer
from tests.test_bambuddy_settings import TEST_SECRET_KEY, answer_with
from tests.test_plate_parts import sliced
from tests.test_production_booking import (
    ARCHIVES,
    DE,
    field_error,
    form,
    productions,
    send,
)
from tests.test_production_page import set_up
from tests.test_stock import add_article


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


def plates_answer(objects: list[str], grams: float, count: int | None = None) -> dict:
    return {
        "archive_id": 1,
        "filename": "beispiel.gcode.3mf",
        "plates": [
            {
                "index": 1,
                "name": objects[0],
                "objects": objects,
                "object_count": count or len(objects),
                "filament_used_grams": grams,
                "filaments": [{"color": "#AA0000", "used_grams": grams}],
            }
        ],
    }


def serve_parts(client: AppClient, plates: dict[int, dict | None], files: dict[int, bytes] | None):
    """Bambuddy: the prints (like `serve`), and per print id the plates and the file."""
    files = files or {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/api/v1/archives/":
            limit, offset = int(request.url.params["limit"]), int(request.url.params["offset"])
            return httpx2.Response(200, json=ARCHIVES[offset : offset + limit])
        match = re.fullmatch(r"/api/v1/archives/(\d+)/(plates|download)", path)
        if match is None:
            return httpx2.Response(404)
        archive_id = int(match[1])
        if match[2] == "plates":
            found = plates.get(archive_id)
            return httpx2.Response(200, json=found) if found else httpx2.Response(404)
        return (
            httpx2.Response(200, content=files[archive_id])
            if archive_id in files
            else httpx2.Response(404)
        )

    answer_with(client, handler)


def prepare(client: AppClient, plates: dict[int, dict | None], files=None) -> int:
    set_up(client)
    add_printer("3.60")
    serve_parts(client, plates, files)
    return add_article()


def value(html: str, name: str) -> str | None:
    match = re.search(rf'<input[^>]*name="{name}"[^>]*value="([^"]*)"', html)
    return htmllib.unescape(match.group(1)) if match else None


def lines_of(html: str, archive_id: int) -> list[tuple[str, str, str, str]]:
    found = []
    for k in range(50):
        if f'name="pname_{archive_id}_{k}"' not in html:
            break
        found.append(
            tuple(
                value(html, f"p{part}_{archive_id}_{k}") or ""
                for part in ("name", "qty", "grams", "waste")
            )
        )
    return found


def mask_fields(html: str, archive_id: int, **changes: str) -> dict[str, object]:
    """What a browser would send back for the mask as it is on the page now."""
    data: dict[str, object] = {
        "prints": [str(archive_id)],
        "parts_mode": re.search(r'name="parts_mode" value="(\w+)" checked', html).group(1),
        "parts_mode_was": value(html, "parts_mode_was") or "",
        f"detected_{archive_id}": value(html, f"detected_{archive_id}") or "",
        "quantity": value(html, "quantity") or "",
        "failed_pieces": value(html, "failed_pieces") or "",
        "quantity_auto": value(html, "quantity_auto") or "",
        "failed_auto": value(html, "failed_auto") or "",
        "date": "",
        "note": "",
    }
    for k, (name, qty, grams, waste) in enumerate(lines_of(html, archive_id)):
        data.update(
            {
                f"pname_{archive_id}_{k}": name,
                f"pqty_{archive_id}_{k}": qty,
                f"pgrams_{archive_id}_{k}": grams,
                f"pwaste_{archive_id}_{k}": waste,
            }
        )
    data.update(changes)
    return data


# Print 6 has two parts (known weights from the file); print 7 has no plates at Bambuddy
TWO = plates_answer(["Cube", "Cylinder"], 44)
FILE = sliced([("Cube", 10.0, 0), ("Cylinder", 30.0, 0)], shared=4.0)


def test_first_visit_shows_the_detected_parts_with_weights(client: AppClient) -> None:
    prepare(client, {6: TWO}, {6: FILE})
    html = client.get("/production/new?prints=6").text
    assert 'name="parts_mode" value="different" checked' in html
    lines = lines_of(html, 6)
    assert [(x[0], x[1], x[3]) for x in lines] == [("Cube", "1", "0"), ("Cylinder", "1", "0")]
    assert lines[0][2] == "11" and lines[1][2] == "33"  # with their share of the tower
    # the good parts: both parts once = one finished article; no waste
    assert value(html, "quantity") == "1" and value(html, "failed_pieces") == ""


def test_1_same_parts_fifty_with_forty_waste(client: AppClient) -> None:
    ten_file = sliced([("Clip", 2.0, 0)] * 50)
    article_id = prepare(client, {6: plates_answer(["Clip"] * 50, 100)}, {6: ten_file})
    html = client.get("/production/new?prints=6").text
    assert 'name="parts_mode" value="same" checked' in html
    assert [(x[0], x[1]) for x in lines_of(html, 6)] == [("Clip", "50")]
    assert value(html, "quantity") == "50"

    # the user types the waste and presses "Apply"
    data = mask_fields(html, 6, pwaste_6_0="40", parts_do="mode")
    html = send(client, data).text
    assert value(html, "quantity") == "10" and value(html, "failed_pieces") == "40"
    assert "80 %" in html

    # a direct "Book" without "Apply" works the same (the fields follow the lines)
    data = mask_fields(html, 6, pwaste_6_0="40", article=str(article_id), action="book")
    data["quantity"], data["failed_pieces"] = "50", ""  # as the page had them before
    data["quantity_auto"], data["failed_auto"] = "50", ""
    response = send(client, data)
    assert response.status_code == 303
    (production,) = productions()
    assert production.results[0].quantity == 10 and production.failed_pieces == 40


def test_2_lantern_quote_by_weight(client: AppClient) -> None:
    weights = [("Top", 5.0, 0), ("Roof", 9.0, 0), ("Wall", 14.0, 0), ("Base", 21.0, 0)]
    weights.append(("Floor", 33.0, 0))
    names = [w[0] for w in weights]
    prepare(client, {6: plates_answer(names, 82)}, {6: sliced(weights)})
    html = client.get("/production/new?prints=6").text
    assert [x[0] for x in lines_of(html, 6)] == ["Floor", "Roof", "Top", "Wall", "Base"] or len(
        lines_of(html, 6)
    ) == 5
    base = next(k for k, x in enumerate(lines_of(html, 6)) if x[0] == "Base")
    data = mask_fields(html, 6, parts_do="mode", **{f"pwaste_6_{base}": "1"})
    html = send(client, data).text
    assert "25,6 %" in html  # 21 / 82
    assert value(html, "quantity") == "0"  # the 21 g part is missing: no finished lantern
    assert value(html, "failed_pieces") == "1"


def test_3_mode_changes_the_mask_and_hand_entry_works(client: AppClient) -> None:
    prepare(client, {6: TWO}, {6: FILE})
    html = client.get("/production/new?prints=6").text
    # different -> same: one line with quantity 2
    same = send(client, mask_fields(html, 6, parts_mode="same", parts_do="mode")).text
    assert [(x[0], x[1]) for x in lines_of(same, 6)] == [("Cube", "2")]
    assert value(same, "quantity") == "2"
    # and back: the parts found in the file
    back = send(client, mask_fields(same, 6, parts_mode="different", parts_do="mode")).text
    assert [(x[0], x[1]) for x in lines_of(back, 6)] == [("Cube", "1"), ("Cylinder", "1")]

    # no data at Bambuddy (print 7): both modes start with an empty line to type into
    html = client.get("/production/new?prints=7").text
    assert 'name="parts_mode" value="same" checked' not in html
    assert DE["production.parts.mode_none"] in html
    for mode in ("same", "different"):
        data = {"prints": ["7"], "parts_mode": mode, "parts_do": "mode", "quantity": ""}
        html = send(client, data).text
        assert lines_of(html, 7) == [("", "", "", "")]
    data = mask_fields(
        html,
        7,
        parts_mode="different",
        pname_7_0="Hand",
        pqty_7_0="4",
        pwaste_7_0="1",
        parts_do="mode",
    )
    html = send(client, data).text
    assert value(html, "quantity") == "3" and value(html, "failed_pieces") == "1"
    assert DE["production.parts.by_pieces"] in html and "25 %" in html


def test_4_merge_and_split(client: AppClient) -> None:
    prepare(client, {6: TWO}, {6: FILE})
    html = client.get("/production/new?prints=6").text
    data = mask_fields(html, 6, parts_do="merge:6", ppick_6_0="1", ppick_6_1="1")
    merged = send(client, data).text
    assert [(x[0], x[1]) for x in lines_of(merged, 6)] == [("Cube", "2")]
    assert lines_of(merged, 6)[0][2] == "22"  # (11 + 33) / 2
    parted = send(client, mask_fields(merged, 6, parts_do="split:6:0")).text
    assert [(x[0], x[1]) for x in lines_of(parted, 6)] == [("Cube", "1"), ("Cube", "1")]
    dropped = send(client, mask_fields(parted, 6, parts_do="drop:6:1")).text
    assert len(lines_of(dropped, 6)) == 1
    added = send(client, mask_fields(dropped, 6, parts_do="add:6")).text
    assert len(lines_of(added, 6)) == 2


@pytest.mark.parametrize(
    ("changes", "key"),
    [
        ({"pwaste_6_0": "-1"}, "production.error.parts_waste"),
        ({"pwaste_6_0": "2"}, "production.error.parts_waste"),
        ({"pwaste_6_0": "viele"}, "production.error.parts_waste"),
        ({"pqty_6_0": "0"}, "production.error.parts_quantity"),
        ({"pqty_6_0": "x"}, "production.error.parts_quantity"),
        ({"pgrams_6_0": "-3"}, "production.error.parts_grams"),
        ({"pgrams_6_0": "schwer"}, "production.error.parts_grams"),
    ],
)
def test_5_invalid_input_is_refused_and_nothing_is_booked(
    client: AppClient, changes: dict[str, str], key: str
) -> None:
    article_id = prepare(client, {6: TWO}, {6: FILE})
    html = client.get("/production/new?prints=6").text
    data = mask_fields(html, 6, article=str(article_id), action="book", **changes)
    response = send(client, data)
    assert response.status_code == 422
    assert field_error(response.text, "parts_6_0") == DE[key]
    assert productions() == []
    # what was typed stays in the form
    assert value(response.text, next(iter(changes))) == next(iter(changes.values()))


def test_6_rights_and_form_token(client: AppClient) -> None:
    prepare(client, {6: TWO}, {6: FILE})
    html = client.get("/production/new?prints=6").text
    data = mask_fields(html, 6, parts_do="add:6")
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        reader.app.state.bambuddy_transport = client.app.state.bambuddy_transport
        assert reader.get("/production/new?prints=6").status_code == 403
        assert send(reader, data).status_code == 403
    client.csrf_token = None
    for token in ({}, {"csrf_token": "falsch"}):
        assert client.post("/production/new", data={**data, **token}).status_code == 403


def test_8_several_plates_the_replacement_part_counts(client: AppClient) -> None:
    names = ["T0", "T1", "T2"]
    weights = [("T0", 10.0, 0), ("T1", 12.0, 0), ("T2", 14.0, 0)]
    plates = {6: plates_answer(names, 36), 7: plates_answer(["T2"], 14)}
    files = {6: sliced(weights), 7: sliced([("T2", 14.0, 0)])}
    prepare(client, plates, files)
    html = client.get("/production/new?prints=6&prints=7").text
    assert len(lines_of(html, 6)) == 3 and len(lines_of(html, 7)) == 1
    t2 = next(k for k, x in enumerate(lines_of(html, 6)) if x[0] == "T2")
    data = mask_fields(html, 6, parts_do="mode", **{f"pwaste_6_{t2}": "1"})
    for k, (name, qty, grams, waste) in enumerate(lines_of(html, 7)):
        data.update(
            {
                f"pname_7_{k}": name,
                f"pqty_7_{k}": qty,
                f"pgrams_7_{k}": grams,
                f"pwaste_7_{k}": waste,
            }
        )
    data["prints"] = ["6", "7"]
    data["detected_7"] = value(html, "detected_7") or ""
    html = send(client, data).text
    # T2 is scrap on plate 6, good on plate 7: one finished article, one scrap piece
    assert value(html, "quantity") == "1" and value(html, "failed_pieces") == "1"


def test_9_missing_weights_count_by_pieces(client: AppClient) -> None:
    # Bambuddy lists the objects, the file is not usable: no weights, quote by pieces
    prepare(client, {6: plates_answer(["A", "A", "B", "B"], 20)}, {6: b"damaged"})
    html = client.get("/production/new?prints=6").text
    assert [(x[0], x[1], x[2]) for x in lines_of(html, 6)] == [("A", "2", ""), ("B", "2", "")]
    data = mask_fields(html, 6, pwaste_6_0="1", parts_do="mode")
    html = send(client, data).text
    assert DE["production.parts.by_pieces"] in html and "25 %" in html


def test_without_a_mode_the_old_fields_work_as_before(client: AppClient) -> None:
    article_id = prepare(client, {}, {})
    html = client.get("/production/new?prints=6").text
    assert 'name="parts_mode" value="same" checked' not in html
    response = send(client, form([6], (article_id, "3"), failed_pieces="2"))
    assert response.status_code == 303
    (production,) = productions()
    assert production.results[0].quantity == 3 and production.failed_pieces == 2


def test_the_new_names_are_used(client: AppClient) -> None:
    prepare(client, {6: TWO}, {6: FILE})
    html = client.get("/production/new?prints=6").text
    assert f">{DE['production.col.quantity']}</label>" in html
    assert DE["production.col.quantity"] == "Gutteile"
    assert DE["production.field.failed_pieces"] == "Ausschuss"
    assert DE["production.detail.failed_pieces"] == "Ausschuss"
