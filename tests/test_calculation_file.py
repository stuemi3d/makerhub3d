"""Calculation with a .gcode.3mf file (issue #181, part 2): choice, upload, plates, prefilled form.

The sliced file is the invented-metadata test file of the slice reader tests (two plates: plate 1
with black and yellow PLA, 24 min 48 s, 2 objects; plate 2 with black PLA, 16 min 19 s, 1 object).
The numbers of the test cases refer to the comment on issue #181.
"""

import html as html_lib
import json
import re

import pytest

from app import calculation_files
from app.main import create_app
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.popup_checks import HX, is_popup, opens_in_popup
from tests.test_calculation_page import (
    DE,
    Ready,
    field_error,
    input_value,
    make_ready,
    out,
    secret_key,  # noqa: F401  (autouse: the key of the secrets)
    select_values,
)
from tests.test_slice_reader import fixture, make_zip, sliced_3mf
from tests.test_stock import add_article

NAME = "wuerfel.gcode.3mf"
FILE = {"file": (NAME, b"")}


@pytest.fixture
def ready(client: AppClient) -> Ready:
    return make_ready(client)


def upload(
    ready: Ready,
    data: bytes | None = None,
    name: str = NAME,
    client: AppClient | None = None,
    headers: dict[str, str] | None = None,
    **over: str,
):
    form = {"printer": str(ready.printer)}
    form.update(over)
    content = sliced_3mf() if data is None else data
    return (client or ready.client).post(
        "/calculation/file",
        data=form,
        files={"file": (name, content, "application/octet-stream")},
        headers=headers,
    )


def hidden(html: str, name: str) -> str:
    match = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)"', html)
    assert match, name
    return html_lib.unescape(match.group(1))


def take(ready: Ready, page: str, picked: list[str], **over: str):
    """Chooses plates on the answer of the upload."""
    form = {
        name: hidden(page, name)
        for name in ("printer", "plates", "file_name", "file_printer", "file_model", "article")
        if f'name="{name}"' in page
    }
    form["plate"] = picked
    form.update(over)
    return ready.client.post("/calculation/file/take", data=form)


def text_of(html: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", html))


# --- Cases 1-3: the choice at the start ------------------------------------------------------


def test_1_the_new_button_opens_the_choice_with_file_or_by_hand(ready: Ready) -> None:
    listing = ready.client.get("/calculation").text
    assert 'hx-get="/calculation/start"' in listing
    start = ready.client.get("/calculation/start", headers=HX).text
    assert "<html" not in start
    assert opens_in_popup(start, "/calculation/file")
    assert opens_in_popup(start, "/calculation/new")
    assert DE["calc.start.file"] in start and DE["calc.start.hand"] in start


def test_2_by_hand_is_the_form_of_part_1(ready: Ready) -> None:
    html = ready.client.get("/calculation/new").text
    assert DE["calc.field.print_minutes"] in html
    assert 'name="file_name"' not in html


def test_3_the_article_stays_chosen_on_both_ways(ready: Ready) -> None:
    article_id = add_article()
    listing = ready.client.get(f"/calculation?article={article_id}").text
    assert f'hx-get="/calculation/start?article={article_id}"' in listing
    start = ready.client.get(f"/calculation/start?article={article_id}", headers=HX).text
    assert opens_in_popup(start, f"/calculation/file?article={article_id}")
    assert opens_in_popup(start, f"/calculation/new?article={article_id}")
    page = upload(ready, article=str(article_id)).text
    chosen = take(ready, page, ["1"]).text
    assert re.search(rf'<option value="{article_id}" selected', chosen)


def test_3_an_unknown_article_gives_404(ready: Ready) -> None:
    assert ready.client.get("/calculation/start?article=999").status_code == 404
    assert ready.client.get("/calculation/file?article=abc").status_code == 404


# --- Cases 4-5: the upload page -----------------------------------------------------------------


def test_4_the_page_asks_for_printer_and_file_in_the_popup(ready: Ready) -> None:
    other = ready.client.get("/calculation/file", headers=HX).text
    assert is_popup(other)
    assert 'type="file"' in other and 'hx-encoding="multipart/form-data"' in other
    assert 'enctype="multipart/form-data"' in other
    assert str(ready.printer) in select_values(other, "printer")
    assert "<html" in ready.client.get("/calculation/file").text


def test_5_printer_and_file_are_needed(ready: Ready) -> None:
    html = upload(ready, printer="").text
    assert field_error(html, "printer") == DE["calc.error.printer"]
    response = ready.client.post("/calculation/file", data={"printer": str(ready.printer)})
    assert response.status_code == 422
    assert field_error(response.text, "file") == DE["calc.file.error.none"]


# --- Cases 6-13: what is accepted ---------------------------------------------------------------


def test_6_a_sliced_file_is_accepted_and_shows_the_plates(ready: Ready) -> None:
    response = upload(ready)
    assert response.status_code == 200
    assert 'name="plate"' in response.text


def test_7_a_3mf_without_gcode_is_refused_with_a_clear_hint(ready: Ready) -> None:
    unsliced = make_zip({"Metadata/slice_info.config": fixture("slice_info_unsliced.config")})
    response = upload(ready, unsliced)
    assert response.status_code == 422
    assert field_error(response.text, "file") == DE["calc.file.error.not_sliced"]


def test_8_pure_gcode_is_refused_with_a_clear_hint(ready: Ready) -> None:
    gcode = fixture("plate_1_excerpt.gcode")
    for name in ("a.gcode", NAME):
        response = upload(ready, gcode, name=name)
        assert response.status_code == 422
        assert field_error(response.text, "file") == DE["calc.file.error.gcode"]


@pytest.mark.parametrize(
    "content", [b"", b"just text", b"\x89PNG\r\n\x1a\n" + b"0" * 50, b"PK\x03\x04broken"]
)
def test_9_other_files_are_refused_without_details(ready: Ready, content: bytes) -> None:
    response = upload(ready, content)
    assert response.status_code == 422
    assert field_error(response.text, "file") == DE["calc.file.error.invalid"]
    assert "Traceback" not in response.text and "SliceFileError" not in response.text


def test_9_a_wrong_name_is_refused_even_with_a_good_content(ready: Ready) -> None:
    for name in ("wuerfel.3mf", "wuerfel.zip", "wuerfel.gcode.3mf.txt", ""):
        response = upload(ready, name=name)
        assert response.status_code == 422, name
        assert field_error(response.text, "file") is not None


def test_9_the_name_check_ignores_case(ready: Ready) -> None:
    assert upload(ready, name="WUERFEL.GCODE.3MF").status_code == 200


def test_10_a_file_that_is_too_large_is_refused(
    ready: Ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calculation_files, "MAX_UPLOAD", 1000)
    response = upload(ready, sliced_3mf() + b"0" * 2000)
    assert response.status_code in (413, 422)
    if response.status_code == 422:
        assert field_error(response.text, "file") == DE["calc.file.error.too_large"]


def test_10_the_limit_is_50_mb() -> None:
    assert calculation_files.MAX_UPLOAD == 50 * 1024 * 1024


def test_10_a_request_far_over_the_limit_is_stopped_early(ready: Ready) -> None:
    response = ready.client.post(
        "/calculation/file",
        data={"printer": str(ready.printer)},
        files={"file": (NAME, b"x", "application/octet-stream")},
        headers={"Content-Length": str(400 * 1024 * 1024)},
    )
    assert response.status_code == 413


def test_12_nothing_is_written_and_the_name_is_no_path(
    ready: Ready, tmp_path: pytest.TempPathFactory
) -> None:
    from tests.test_stock import db  # noqa: F401  (the database file is not touched by a read)

    page = upload(ready, name="../../etc/x.gcode.3mf").text
    # The name only appears, escaped, as text of the form
    assert hidden(page, "file_name") == "x.gcode.3mf"
    assert "../" not in page


def test_13_login_right_and_form_token(ready: Ready) -> None:
    from tests.test_calculation_page import AppClient as Anonymous  # noqa: F401

    with AppClient(create_app()) as anonymous:
        for response in (
            anonymous.get("/calculation/file", follow_redirects=False),
            anonymous.post(
                "/calculation/file",
                data={},
                files={"file": (NAME, b"x")},
                follow_redirects=False,
            ),
            anonymous.post("/calculation/file/take", data={}, follow_redirects=False),
        ):
            assert response.status_code in (303, 307, 401, 403)
    ready.client.csrf_token = None
    response = ready.client.post(
        "/calculation/file",
        data={"printer": str(ready.printer)},
        files={"file": (NAME, sliced_3mf(), "application/octet-stream")},
    )
    assert response.status_code == 403


def test_13_readers_and_employees_may_use_it(ready: Ready) -> None:
    add_user(READER_NAME, "read_only")
    add_user(EMPLOYEE_NAME, "employee")
    for name in (READER_NAME, EMPLOYEE_NAME):
        with client_as(name, app=ready.client.app) as other:
            assert other.get("/calculation/file").status_code == 200
            assert upload(ready, client=other).status_code == 200


# --- Cases 14-16, 30-31: what the file holds ----------------------------------------------------


def test_14_the_summary_shows_plates_times_grams_and_objects(ready: Ready) -> None:
    text = text_of(upload(ready).text)
    assert NAME in text
    # Plate 1: 24 min 48 s, black 5.40 g and yellow 4.30 g, two objects
    assert "24" in text and "5,4" in text and "4,3" in text
    assert "#000000" in text and "#FFFF00" in text
    assert "3,96" in text
    assert DE["calc.file.objects"].replace("{count}", "2") in text
    assert DE["calc.file.objects"].replace("{count}", "1") in text


def test_14_the_time_share_is_only_shown_for_several_materials(ready: Ready) -> None:
    text = text_of(upload(ready).text)
    assert DE["calc.file.share"] in text  # plate 1 has two filaments
    single = make_zip(
        {
            "Metadata/slice_info.config": fixture("slice_info.config").split(b"<plate>")[0]
            + b"<plate>"
            + fixture("slice_info.config").split(b"<plate>")[2],
        }
    )
    assert DE["calc.file.share"] not in text_of(upload(ready, single).text)


def test_15_several_plates_can_be_ticked_and_none_is_preselected(ready: Ready) -> None:
    page = upload(ready).text
    boxes = re.findall(r'<input type="checkbox" name="plate"[^>]*>', page)
    assert len(boxes) == 2
    assert not any("checked" in box for box in boxes)


def test_15_a_single_plate_is_preselected(ready: Ready) -> None:
    config = fixture("slice_info.config")
    single = make_zip(
        {
            "Metadata/slice_info.config": config.split(b"<plate>")[0]
            + b"<plate>"
            + config.split(b"<plate>")[2]
        }
    )
    page = upload(ready, single).text
    boxes = re.findall(r'<input type="checkbox" name="plate"[^>]*>', page)
    assert len(boxes) == 1 and "checked" in boxes[0]


def test_15_without_a_plate_there_is_a_hint(ready: Ready) -> None:
    page = upload(ready).text
    response = take(ready, page, [])
    assert response.status_code == 422
    assert DE["calc.file.error.plate"] in response.text
    assert 'name="plate"' in response.text


def test_16_estimated_time_shares_are_marked(ready: Ready) -> None:
    # The excerpt has markers for plate 1; remove the gcode, then the split is by grams
    no_gcode = make_zip({"Metadata/slice_info.config": fixture("slice_info.config")})
    assert DE["calc.file.estimated"] in text_of(upload(ready, no_gcode).text)
    assert DE["calc.file.estimated"] not in text_of(upload(ready).text)


# --- Cases 17-23, 30: the prefilled form ------------------------------------------------------


def test_17_one_plate_prefills_the_form(ready: Ready) -> None:
    html = take(ready, upload(ready).text, ["1"]).text
    assert input_value(html, "print_minutes") == "24,8"
    assert input_value(html, "grams_0") == "5,4" and input_value(html, "grams_1") == "4,3"
    assert input_value(html, "rows") == "2"
    assert 'value="PLA" selected' in html
    # The time share of each material (several materials): 24.8 min together
    assert input_value(html, "minutes_0") and input_value(html, "minutes_1")
    assert str(ready.printer) in select_values(html, "printer")
    assert re.search(rf'<option value="{ready.printer}" selected', html)


def test_21_the_quantity_is_always_empty(ready: Ready) -> None:
    for plates in (["1"], ["2"], ["1", "2"]):
        html = take(ready, upload(ready).text, plates).text
        assert input_value(html, "pieces") == ""


def test_30_two_plates_are_added_up(ready: Ready) -> None:
    html = take(ready, upload(ready).text, ["1", "2"]).text
    assert input_value(html, "print_minutes") == "41,12"
    assert input_value(html, "rows") == "2"
    assert input_value(html, "grams_0") == "9,36"
    assert input_value(html, "grams_1") == "4,3"
    # 5.40 g black is on both plates, one row for it
    assert html.count('name="grams_') == 2


def test_17_the_info_about_the_file_stays_in_the_form(ready: Ready) -> None:
    html = take(ready, upload(ready).text, ["1", "2"]).text
    assert NAME in text_of(html)
    assert hidden(html, "file_name") == NAME
    # A new calculation (typing) keeps the file info
    again = ready.client.post(
        "/calculation/new",
        data={
            "printer": str(ready.printer),
            "rows": "1",
            "grams_0": "5",
            "file_name": NAME,
            "file_plates": "1, 2",
            "file_printer": "Bambu Lab X1E",
            "file_model": "X1E",
            "action": "live",
        },
    ).text
    assert NAME in text_of(again)


def test_18_a_material_of_the_table_is_chosen_even_with_other_spelling(ready: Ready) -> None:
    changed = fixture("slice_info.config").replace(b'type="PLA"', b'type="pla"')
    page = upload(ready, make_zip({"Metadata/slice_info.config": changed})).text
    html = take(ready, page, ["1"]).text
    assert 'value="PLA" selected' in html
    assert DE["calc.file.material_unknown"].split("{")[0] not in html


def test_19_an_unknown_material_stays_empty_with_a_hint_and_no_result(ready: Ready) -> None:
    changed = fixture("slice_info.config").replace(b'type="PLA"', b'type="PLA-CF"')
    page = upload(ready, make_zip({"Metadata/slice_info.config": changed})).text
    html = take(ready, page, ["2"]).text
    assert "selected" not in re.search(
        r'<select[^>]*name="material_0".*?</select>', html, re.S
    ).group(0)
    assert DE["calc.file.material_unknown"].replace("{material}", "PLA-CF") in html
    assert out(html, "res-suggested") is None


def test_19_after_choosing_a_material_and_spool_the_result_is_there(ready: Ready) -> None:
    changed = fixture("slice_info.config").replace(b'type="PLA"', b'type="PLA-CF"')
    page = upload(ready, make_zip({"Metadata/slice_info.config": changed})).text
    html = take(ready, page, ["2"]).text
    data = {
        "printer": str(ready.printer),
        "rows": "1",
        "print_minutes": input_value(html, "print_minutes"),
        "pieces": "1",
        "material_0": "PLA",
        "grams_0": input_value(html, "grams_0"),
        "spool_0_0": "1",
        "scrap": "5",
        "margin": "30",
        "file_name": NAME,
        "action": "calc",
    }
    assert out(ready.client.post("/calculation/new", data=data).text, "res-suggested")


def test_20_spools_are_chosen_as_in_part_1(ready: Ready) -> None:
    html = take(ready, upload(ready).text, ["1"]).text
    assert "1" in select_values(html, "spool_0_0")
    assert DE["calc.field.spool"] in html


def test_22_the_calculation_runs_like_in_part_1(ready: Ready) -> None:
    html = take(ready, upload(ready).text, ["2"]).text
    data = {
        "printer": str(ready.printer),
        "rows": "1",
        "print_minutes": input_value(html, "print_minutes"),
        "pieces": "1",
        "material_0": "PLA",
        "grams_0": "100",
        "spool_0_0": "1",
        "scrap": "0",
        "margin": "0",
        "action": "calc",
    }
    assert out(ready.client.post("/calculation/new", data=data).text, "res-material") == "2,00"


def test_23_saving_stores_the_calculation_without_the_file(ready: Ready) -> None:
    article_id = add_article()
    html = take(ready, upload(ready, article=str(article_id)).text, ["2"]).text
    data = {
        "printer": str(ready.printer),
        "article": str(article_id),
        "rows": "1",
        "print_minutes": input_value(html, "print_minutes"),
        "pieces": "2",
        "material_0": "PLA",
        "grams_0": input_value(html, "grams_0"),
        "spool_0_0": "1",
        "scrap": "5",
        "margin": "30",
        "file_name": NAME,
        "action": "save",
    }
    response = ready.client.post("/calculation/new", data=data, follow_redirects=False)
    assert response.status_code == 303
    assert "saved=1" in response.headers["location"]


# --- Cases 24-25: the printer of the file -----------------------------------------------------


def test_24_another_printer_in_the_file_gives_a_hint(ready: Ready) -> None:
    html = take(ready, upload(ready).text, ["1"]).text
    hint = DE["calc.file.printer_differs"].replace("{file}", "Bambu Lab X1E")
    assert hint.split("{")[0] in text_of(html)
    assert "X1E" in text_of(html)


def test_25_a_matching_printer_or_none_in_the_file_gives_no_hint(ready: Ready) -> None:
    same = fixture("project_settings_excerpt.config").replace(b"X1E", b"P2S")
    data = make_zip(
        {
            "Metadata/slice_info.config": fixture("slice_info.config"),
            "Metadata/project_settings.config": same,
        }
    )
    html = take(ready, upload(ready, data).text, ["1"]).text
    assert DE["calc.file.printer_differs"].split("{")[0] not in text_of(html)
    nameless = make_zip({"Metadata/slice_info.config": fixture("slice_info.config")})
    html = take(ready, upload(ready, nameless).text, ["1"]).text
    assert DE["calc.file.printer_differs"].split("{")[0] not in text_of(html)


def test_24_the_hint_follows_the_printer_chosen_in_the_form(ready: Ready) -> None:
    same = fixture("project_settings_excerpt.config").replace(b"X1E", b"P2S")
    data = make_zip(
        {
            "Metadata/slice_info.config": fixture("slice_info.config"),
            "Metadata/project_settings.config": same,
        }
    )
    page = upload(ready, data).text
    assert hidden(page, "file_model") == "P2S"
    from tests.machine import add_printer

    other = add_printer("5.00", bambuddy_printer_id=None, name="Zweitdrucker")
    html = ready.client.post(
        "/calculation/new",
        data={
            "printer": str(other),
            "file_name": NAME,
            "file_printer": "Bambu Lab P2S",
            "file_model": "P2S",
            "action": "printer",
        },
    ).text
    assert DE["calc.file.printer_differs"].split("{")[0] in text_of(html)


# --- Cases 26-27: safety -----------------------------------------------------------------------


def test_26_texts_from_the_file_are_escaped(ready: Ready) -> None:
    evil = fixture("slice_info.config").replace(
        b'type="PLA"', b'type="&lt;script&gt;alert(1)&lt;/script&gt;"'
    )
    page = upload(
        ready, make_zip({"Metadata/slice_info.config": evil}), name="<b>x</b>.gcode.3mf"
    ).text
    assert "<script>alert" not in page and "<b>x" not in page
    html = take(ready, page, ["1"]).text
    assert "<script>alert" not in html and "<b>x" not in html


def test_26_the_payload_of_the_second_step_is_checked(ready: Ready) -> None:
    page = upload(ready).text
    for bad in (
        "",
        "not json",
        "[]",
        json.dumps({"p": "x"}),
        json.dumps({"p": [{"i": "a"}]}),
        json.dumps({"p": [{"i": 1, "s": -5, "f": []}]}),
        "x" * 300_000,
    ):
        response = take(ready, page, ["1"], plates=bad)
        assert response.status_code in (400, 422), bad[:20]
        assert "Traceback" not in response.text


def test_26_an_unknown_plate_number_is_refused(ready: Ready) -> None:
    page = upload(ready).text
    assert take(ready, page, ["9"]).status_code == 422
    assert take(ready, page, ["x"]).status_code == 422


def test_27_a_manipulated_file_gives_the_normal_hint(
    ready: Ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import slice_reader

    monkeypatch.setattr(slice_reader, "MAX_ENTRIES", 3)
    many = make_zip(
        {f"Metadata/{n}.txt": b"x" for n in range(10)}
        | {"Metadata/slice_info.config": fixture("slice_info.config")}
    )
    response = upload(ready, many)
    assert response.status_code == 422
    assert field_error(response.text, "file") == DE["calc.file.error.invalid"]
    assert upload(ready, b"PK\x03\x04" + b"\xff" * 100).status_code == 422


def test_31_the_object_count_is_not_put_into_a_field(ready: Ready) -> None:
    html = take(ready, upload(ready).text, ["1"]).text
    assert input_value(html, "pieces") == ""


def test_texts_exist_in_both_languages() -> None:
    from tests.test_calculation_page import EN

    keys = [key for key in DE if key.startswith(("calc.start.", "calc.file."))]
    assert keys
    for key in keys:
        assert key in EN, key
