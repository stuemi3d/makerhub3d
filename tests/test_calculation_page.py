"""Page "New calculation" (issue #161, part a): calculating by hand with live results, no saving.
All data is invented; Bambuddy answers come from a mock. The test case numbers refer to the
comment on issue #161.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

from app.main import create_app
from app.models import AppSettings, Device, PrinterModel, PrinterModelPower
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.machine import add_dryer, add_printer
from tests.test_bambuddy_settings import TEST_SECRET_KEY, answer_with
from tests.test_production_page import set_up
from tests.test_stock import add_article, db
from tests.test_templates import make_template

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
BUSINESS = "Geschäftlich"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


def spool(
    spool_id: int,
    material: str,
    cost: float,
    label: int = 1000,
    used: float = 0,
    category: str | None = BUSINESS,
) -> dict:
    return {
        "id": spool_id,
        "material": material,
        "color_name": f"Farbe{spool_id}",
        "brand": "Beispielmarke",
        "cost_per_kg": cost,
        "label_weight": label,
        "weight_used": used,
        "category": category,
    }


SPOOLS = [
    spool(1, "PLA", 20),
    spool(2, "PLA", 30),
    spool(3, "PLA", 10, category=None),  # a private spool
    spool(4, "PETG", 25),
    spool(5, "PLA", 20, label=100, used=40),  # 60 g left
]


def serve_spools(client: AppClient, spools: list[dict] | None = None, status: int = 200) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path != "/api/v1/inventory/spools":
            return httpx2.Response(404)
        return httpx2.Response(status, json=SPOOLS if spools is None else spools)

    answer_with(client, handler)


@dataclass
class Ready:
    client: AppClient
    printer: int


def make_ready(client: AppClient, table: dict[str, str] | None = None) -> Ready:
    """A printer with the model P2S (PLA 0.10, PETG 0.13 kWh/h) at 12.00 EUR/h."""
    set_up(client)
    serve_spools(client)
    printer = add_printer("12.00")
    with db() as session, session.begin():
        model = PrinterModel(name="P2S", created_at=datetime(2026, 1, 1))
        session.add(model)
        session.flush()
        for position, (material, kwh) in enumerate(
            (table or {"PLA": "0.10", "PETG": "0.13"}).items()
        ):
            session.add(
                PrinterModelPower(
                    model_id=model.id,
                    material=material,
                    kwh_per_hour=Decimal(kwh),
                    position=position,
                )
            )
        session.get(Device, printer).printer_model_id = model.id
    return Ready(client, printer)


def set_business_only(value: bool = True) -> None:
    """The setting "only business spools" (issue #186); off is the default."""
    with db() as session, session.begin():
        session.get(AppSettings, 1).spool_business_only = value


@pytest.fixture
def ready(client: AppClient) -> Ready:
    return make_ready(client)


def values(ready: Ready, **over: str) -> dict[str, str]:
    """A complete form: 1 plate with 2 pieces, 60 min, 100 g PLA from spool 1 (20 EUR/kg)."""
    data = {
        "printer": str(ready.printer),
        "print_minutes": "60",
        "pieces": "2",
        "rows": "1",
        "material_0": "PLA",
        "grams_0": "100",
        "minutes_0": "60",
        "spool_0_0": "1",
        "setup_minutes": "",
        "rework_minutes": "",
        "scrap": "5",
        "margin": "30",
        "action": "calc",
    }
    data.update(over)
    return data


def calc(ready: Ready, client: AppClient | None = None, **over: str):
    return (client or ready.client).post("/calculation/new", data=values(ready, **over))


def out(html: str, name: str) -> str | None:
    match = re.search(rf'<output id="{name}"[^>]*>(.*?)</output>', html, re.S)
    return match.group(1).strip() if match else None


def select_values(html: str, name: str) -> list[str]:
    block = re.search(rf'<select\b[^>]*name="{name}".*?</select>', html, re.S)
    return re.findall(r'<option value="([^"]*)"', block.group(0)) if block else []


def input_value(html: str, name: str) -> str | None:
    match = re.search(rf'<input\b[^>]*name="{name}"[^>]*value="([^"]*)"', html)
    return match.group(1) if match else None


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.S)
    return match.group(1).strip() if match else None


# --- Cases 4-5: where the page is, who may use it --------------------------------------------


def test_4_the_article_list_offers_a_new_calculation_the_template_does_not(
    ready: Ready,
) -> None:
    article_id = add_article()
    html = ready.client.get(f"/calculation?article={article_id}").text
    assert f'hx-get="/calculation/new?article={article_id}"' in html
    assert DE["calc.new"] in html

    assert make_template(ready.client).status_code == 303
    detail = ready.client.get("/templates/1").text
    assert "/calculation/new?article" not in detail
    assert DE["calc.new"] not in detail


def test_4_an_unknown_article_gives_404(ready: Ready) -> None:
    assert ready.client.get("/calculation/new?article=999").status_code == 404
    assert ready.client.get("/calculation/new?article=abc").status_code == 404


def test_4_the_page_opens_without_an_article_as_a_quick_calculation(ready: Ready) -> None:
    response = ready.client.get("/calculation/new")
    assert response.status_code == 200
    assert DE["calc.title"] in response.text


def test_4_readers_and_employees_may_look_and_calculate(ready: Ready) -> None:
    add_user(READER_NAME, "read_only")
    add_user(EMPLOYEE_NAME, "employee")
    for name in (READER_NAME, EMPLOYEE_NAME):
        with client_as(name, app=ready.client.app) as other:
            assert other.get("/calculation/new").status_code == 200
            assert out(calc(ready, other).text, "res-suggested") == "9,90"


def test_5_the_page_needs_a_login(ready: Ready) -> None:
    with AppClient(create_app()) as anonymous:
        for response in (
            anonymous.get("/calculation/new", follow_redirects=False),
            anonymous.post("/calculation/new", data={}, follow_redirects=False),
        ):
            assert response.status_code in (303, 307, 401, 403)
            assert "/login" in response.headers.get("location", "/login")


def test_the_post_needs_the_form_token(ready: Ready) -> None:
    data = values(ready)
    ready.client.csrf_token = None
    assert ready.client.post("/calculation/new", data=data).status_code == 403


# --- Case 6-8: printer and materials ---------------------------------------------------------


def test_6_only_active_printers_can_be_chosen(ready: Ready) -> None:
    other = add_printer("5.00", bambuddy_printer_id=None, name="Zweitdrucker")
    off = add_printer("5.00", bambuddy_printer_id=None, name="Altdrucker", active=False)
    dryer = add_dryer("100")

    html = ready.client.get("/calculation/new").text

    chosen = select_values(html, "printer")
    assert str(ready.printer) in chosen and str(other) in chosen
    assert str(off) not in chosen and str(dryer) not in chosen


def test_6_without_a_printer_there_is_no_result_and_a_hint(ready: Ready) -> None:
    html = calc(ready, printer="").text
    assert out(html, "res-suggested") is None
    assert field_error(html, "printer") == DE["calc.error.printer"]


def test_6_a_deactivated_printer_is_refused_even_when_sent(ready: Ready) -> None:
    off = add_printer("5.00", bambuddy_printer_id=None, name="Altdrucker", active=False)
    html = calc(ready, printer=str(off)).text
    assert out(html, "res-suggested") is None
    assert field_error(html, "printer") == DE["calc.error.printer"]


def test_7_a_printer_without_a_model_gives_a_hint_with_a_link(ready: Ready) -> None:
    bare = add_printer("5.00", bambuddy_printer_id=None, name="Ohne Modell")
    html = calc(ready, printer=str(bare)).text
    assert out(html, "res-suggested") is None
    assert DE["calc.printer.no_model"] in html
    assert 'href="/settings/devices"' in html


def test_7_a_model_without_a_table_gives_a_hint_with_a_link(client: AppClient) -> None:
    set_up(client)
    serve_spools(client)
    printer = add_printer("5.00")
    with db() as session, session.begin():
        model = PrinterModel(name="Leer", created_at=datetime(2026, 1, 1))
        session.add(model)
        session.flush()
        session.get(Device, printer).printer_model_id = model.id
    html = client.post("/calculation/new", data={"printer": str(printer), "action": "printer"}).text
    assert DE["calc.printer.no_table"] in html
    assert 'href="/settings/devices/models"' in html
    assert out(html, "res-suggested") is None


def test_8_the_material_comes_from_the_table_of_the_model_in_its_order(client: AppClient) -> None:
    ready = make_ready(client, {"PETG": "0.13", "ABS": "0.2", "PLA": "0.10"})
    html = calc(ready).text
    assert select_values(html, "material_0") == ["", "PETG", "ABS", "PLA"]


def test_8_a_material_that_is_not_in_the_table_is_refused(ready: Ready) -> None:
    html = calc(ready, material_0="PLA-CF").text
    assert out(html, "res-suggested") is None
    assert field_error(html, "material_0") == DE["calc.error.material"]


# --- Case 9: defaults and optional fields ----------------------------------------------------


def test_9_scrap_and_margin_are_prefilled_from_the_settings(ready: Ready) -> None:
    html = ready.client.get("/calculation/new").text
    assert input_value(html, "scrap") == "5"
    assert input_value(html, "margin") == "30"
    assert input_value(html, "setup_minutes") in ("", None)
    assert input_value(html, "rework_minutes") in ("", None)


def test_9_setup_and_rework_may_stay_empty_and_then_count_as_zero(ready: Ready) -> None:
    html = calc(ready).text
    assert out(html, "res-labour") == "0,00"
    assert out(html, "res-wage") == "\u2013"


def test_9_scrap_and_margin_can_be_changed(ready: Ready) -> None:
    html = calc(ready, scrap="0", margin="0").text
    # 14.03 / 2 = 7.015, no scrap, no margin, rounded up to the next x.90
    assert out(html, "res-cost-with") == "7,02"
    assert out(html, "res-suggested") == "7,90"


# --- Cases 10-12: spools ---------------------------------------------------------------------


def test_10_by_default_all_spools_of_the_material_are_offered_without_a_switch(
    ready: Ready,
) -> None:
    html = calc(ready).text
    assert select_values(html, "spool_0_0") == ["", "1", "2", "3", "5"]
    assert 'name="all_spools"' not in html


def test_10_with_the_setting_only_business_spools_are_offered_and_a_switch_shows_all(
    ready: Ready,
) -> None:
    set_business_only()
    html = calc(ready).text
    assert select_values(html, "spool_0_0") == ["", "1", "2", "5"]
    assert 'name="all_spools"' in html

    shown = calc(ready, all_spools="1").text
    assert select_values(shown, "spool_0_0") == ["", "1", "2", "3", "5"]
    assert "4" not in select_values(shown, "spool_0_0")  # PETG is not PLA


def test_10_there_is_no_average_and_no_free_price_field(ready: Ready) -> None:
    html = calc(ready).text
    assert not re.search(r'name="(price|price_per_kg)[^"]*"', html)


def test_10_the_spool_of_another_material_is_refused_even_when_sent(ready: Ready) -> None:
    html = calc(ready, spool_0_0="4").text
    assert out(html, "res-suggested") is None
    assert field_error(html, "spool_0_0") == DE["calc.error.spool"]


def test_10_a_private_spool_is_fine_by_default_and_needs_the_switch_with_the_setting(
    ready: Ready,
) -> None:
    assert out(calc(ready, spool_0_0="3").text, "res-material") == "1,00"
    set_business_only()
    assert out(calc(ready, spool_0_0="3").text, "res-suggested") is None
    assert out(calc(ready, spool_0_0="3", all_spools="1").text, "res-material") == "1,00"


def test_10_hint_to_choose_the_material_first(ready: Ready) -> None:
    html = ready.client.get("/calculation/new").text
    assert DE["calc.spool.choose_material"] in html
    assert DE["calc.spool.choose_material"] not in calc(ready).text


def test_10_hint_when_there_is_no_spool_for_the_material(client: AppClient) -> None:
    ready = make_ready(client)
    serve_spools(client, [spool(4, "PETG", 25)])
    html = calc(ready, spool_0_0="").text
    hint = DE["calc.spool.none_for_material"].replace("{material}", "PLA")
    assert hint in html
    assert DE["calc.spool.none_filtered"] not in html


def test_10_hint_with_the_setting_points_to_the_switch(client: AppClient) -> None:
    ready = make_ready(client)
    set_business_only()
    serve_spools(client, [spool(3, "PLA", 10, category=None)])
    html = calc(ready, spool_0_0="").text
    assert DE["calc.spool.none_filtered"].replace("{material}", "PLA") in html
    assert DE["calc.spool.none_filtered"].replace("{material}", "PLA") != ""
    assert select_values(calc(ready, all_spools="1").text, "spool_0_0") == ["", "3"]


def test_11_a_spool_that_is_too_small_opens_another_row_and_grams_are_spread(
    ready: Ready,
) -> None:
    first = calc(ready, spool_0_0="5").text
    assert 'name="spool_0_1"' in first  # a second spool is asked for
    assert out(first, "res-suggested") is None

    both = calc(ready, spool_0_0="5", spool_0_1="2").text
    # 60 g from spool 5 at 20 EUR/kg + 40 g from spool 2 at 30 EUR/kg
    assert out(both, "res-material") == "2,40"
    assert 'name="spool_0_2"' not in both


def test_11_a_spool_that_is_big_enough_opens_no_second_row(ready: Ready) -> None:
    assert 'name="spool_0_1"' not in calc(ready).text


def test_11_the_same_spool_twice_is_refused(ready: Ready) -> None:
    html = calc(ready, spool_0_0="5", spool_0_1="5").text
    assert out(html, "res-suggested") is None
    assert field_error(html, "spool_0_1") == DE["calc.error.spool_twice"]


# --- Case 12: incomplete means no result -----------------------------------------------------


def test_12_when_no_spool_covers_the_weight_the_position_is_incomplete(ready: Ready) -> None:
    html = calc(ready, grams_0="5000", spool_0_0="1").text
    assert DE["calc.spool.incomplete"] in html
    for name in ("res-material", "res-suggested", "res-cost-with"):
        assert out(html, name) is None


def test_12_no_spool_with_the_material_means_incomplete_never_zero(client: AppClient) -> None:
    ready = make_ready(client)
    serve_spools(client, [spool(4, "PETG", 25)])
    html = calc(ready, spool_0_0="").text
    assert DE["calc.spool.none_for_material"].replace("{material}", "PLA") in html
    assert out(html, "res-suggested") is None


def test_12_a_position_without_a_chosen_spool_is_incomplete(ready: Ready) -> None:
    html = calc(ready, spool_0_0="").text
    assert DE["calc.spool.incomplete"] in html
    assert out(html, "res-suggested") is None


def test_12_a_spool_without_a_price_is_refused(client: AppClient) -> None:
    ready = make_ready(client)
    free = spool(1, "PLA", 0)
    free["cost_per_kg"] = None
    serve_spools(client, [free])
    html = calc(ready).text
    assert out(html, "res-suggested") is None
    assert field_error(html, "spool_0_0") == DE["calc.error.spool_price"]


# --- Case 13: the live result ----------------------------------------------------------------


def test_13_the_result_shows_all_figures(ready: Ready) -> None:
    html = calc(ready).text
    # material 2.00, power 1 h x 0.10 x 0.30 = 0.03, machine 1 h x 12.00
    assert out(html, "res-material") == "2,00"
    assert out(html, "res-power") == "0,03"
    assert out(html, "res-machine") == "12,00"
    # (14.03 / 2) x 1.05 = 7.36575 -> 7,37; x 1.3 = 9.575 -> 9,90; profit 9.90 - 7.36575
    assert out(html, "res-cost-without") == "7,37"
    assert out(html, "res-cost-with") == "7,37"
    assert out(html, "res-suggested") == "9,90"
    assert out(html, "res-profit") == "2,53"


def test_13_the_drying_surcharge_is_part_of_the_machine_rate(ready: Ready) -> None:
    add_dryer("100")  # 100 EUR per year / 1000 print hours = 0.10 EUR per hour
    assert out(calc(ready).text, "res-machine") == "12,10"


def test_13_setup_and_rework_give_labour_cost_and_an_hourly_wage(ready: Ready) -> None:
    html = calc(ready, setup_minutes="6", rework_minutes="3").text
    # (6 / 2 + 3) min = 0.1 h x 15 EUR = 1.50
    assert out(html, "res-labour") == "1,50"
    assert out(html, "res-cost-without") == "7,37"
    assert out(html, "res-cost-with") == "8,87"
    assert out(html, "res-suggested") == "11,90"
    # (11.90 - 7.36575) / 0.1 h
    assert out(html, "res-wage") == "45,34"


def test_13_power_uses_the_time_share_of_every_material(ready: Ready) -> None:
    html = calc(
        ready,
        rows="2",
        material_1="PETG",
        grams_1="50",
        minutes_1="30",
        spool_1_0="4",
        minutes_0="60",
        print_minutes="90",
    ).text
    # 1 h x 0.10 + 0.5 h x 0.13 = 0.165 kWh x 0.30 = 0.0495
    assert out(html, "res-power") == "0,05"
    # 100 g x 20 + 50 g x 25 = 3.25
    assert out(html, "res-material") == "3,25"


def test_13_the_target_price_of_the_article_is_used_for_the_profit(ready: Ready) -> None:
    article_id = add_article(selling_price=Decimal("10.00"))
    response = ready.client.post(
        "/calculation/new", data={**values(ready), "article": str(article_id)}
    )
    # 10.00 - 7.36575
    assert out(response.text, "res-profit") == "2,63"
    assert out(response.text, "res-suggested") == "9,90"


def test_13_an_empty_extra_position_is_ignored(ready: Ready) -> None:
    html = calc(ready, rows="2", material_1="", grams_1="", minutes_1="").text
    assert out(html, "res-material") == "2,00"


def test_13_the_add_row_action_gives_one_more_position(ready: Ready) -> None:
    html = calc(ready, action="add_row").text
    assert 'name="material_1"' in html
    assert 'name="material_2"' not in html
    assert out(html, "res-suggested") == "9,90"


def test_13_the_calculation_is_never_saved(ready: Ready) -> None:
    calc(ready)
    html = ready.client.get("/calculation/new").text
    assert 'name="grams_0"' in html and input_value(html, "grams_0") in ("", None)


def test_13_the_page_has_no_save_button_yet(ready: Ready) -> None:
    assert 'value="save"' not in calc(ready).text


# --- Case 14: wrong input --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("print_minutes", "abc"),
        ("print_minutes", "-5"),
        ("print_minutes", "0"),
        ("pieces", "0"),
        ("pieces", "1,5"),
        ("pieces", "-2"),
        ("pieces", "99999999999"),
        ("grams_0", "x"),
        ("grams_0", "-1"),
        ("setup_minutes", "x"),
        ("rework_minutes", "-1"),
        ("scrap", "abc"),
        ("margin", "-4"),
    ],
)
def test_14_wrong_numbers_give_a_message_next_to_the_field_not_an_error(
    ready: Ready, name: str, text: str
) -> None:
    response = calc(ready, **{name: text})
    assert response.status_code in (200, 422)
    assert field_error(response.text, name)
    assert out(response.text, "res-suggested") is None
    assert "Traceback" not in response.text


def test_14_the_typed_values_stay_in_the_form(ready: Ready) -> None:
    html = calc(ready, print_minutes="abc", grams_0="77").text
    assert input_value(html, "print_minutes") == "abc"
    assert input_value(html, "grams_0") == "77"


def test_14_a_comma_is_a_decimal_separator(ready: Ready) -> None:
    html = calc(ready, print_minutes="60,0", grams_0="100,0").text
    assert out(html, "res-material") == "2,00"


def test_14_a_given_position_needs_grams(ready: Ready) -> None:
    assert field_error(calc(ready, grams_0="").text, "grams_0")


def test_14_with_several_materials_every_time_share_must_be_a_number(ready: Ready) -> None:
    more = {"rows": "2", "material_1": "PETG", "grams_1": "50", "spool_1_0": "4"}
    for name, text in (("minutes_1", ""), ("minutes_1", "x"), ("minutes_0", "-3")):
        html = calc(ready, **more, **{"minutes_1": "30", name: text}).text
        assert field_error(html, name), (name, text)
        assert out(html, "res-suggested") is None


# --- Time share only with several materials (issue #186, cases 15-16) --------------------------


def test_15_one_material_has_no_time_share_field_and_uses_the_print_time(ready: Ready) -> None:
    page = ready.client.get("/calculation/new").text
    assert 'name="minutes_0"' not in page
    # Even a value sent by hand does not count: 1 h x 0.10 kWh/h x 0.30 EUR/kWh
    assert out(calc(ready, minutes_0="5").text, "res-power") == "0,03"


def test_15_the_second_material_shows_the_field_for_both_and_prefills_the_first(
    ready: Ready,
) -> None:
    html = calc(ready, action="add_row", minutes_0="").text
    assert 'name="minutes_0"' in html and 'name="minutes_1"' in html
    assert input_value(html, "minutes_0") == "60"
    assert input_value(html, "minutes_1") in ("", None)


def test_15_the_sum_of_the_time_shares_is_shown_but_not_enforced(ready: Ready) -> None:
    more = {"rows": "2", "material_1": "PETG", "grams_1": "50", "spool_1_0": "4"}
    html = calc(ready, **more, minutes_0="60", minutes_1="30", print_minutes="60").text
    assert out(html, "calc-minutes-sum") is not None
    assert "90" in out(html, "calc-minutes-sum") and "60" in out(html, "calc-minutes-sum")
    assert out(html, "res-suggested") is not None


def test_15_when_the_second_material_stays_empty_the_print_time_counts_again(
    ready: Ready,
) -> None:
    html = calc(ready, rows="2", minutes_0="10", material_1="", grams_1="", minutes_1="").text
    assert out(html, "res-power") == "0,03"


def test_14_the_number_of_rows_is_limited(ready: Ready) -> None:
    response = calc(ready, rows="5000")
    assert response.status_code in (200, 422)
    assert response.text.count('name="material_') <= 20


def test_14_the_text_of_the_input_is_escaped(ready: Ready) -> None:
    attack = "<script>alert(1)</script>"
    html = calc(ready, print_minutes=attack).text
    assert attack not in html


# --- Case 15: Bambuddy is not there ----------------------------------------------------------


def test_15_when_bambuddy_is_unreachable_there_is_a_hint_and_the_page_works(
    ready: Ready,
) -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no connection")

    answer_with(ready.client, broken)
    response = calc(ready)
    assert response.status_code == 200
    assert DE["bambuddy.error.unreachable"] in response.text
    assert out(response.text, "res-suggested") is None


def test_15_when_bambuddy_is_not_set_up_there_is_a_hint(client: AppClient) -> None:
    printer = add_printer("5.00")
    html = client.post("/calculation/new", data={"printer": str(printer)}).text
    assert DE["calc.spools.not_set_up"] in html


def test_15_a_bambuddy_error_does_not_show_the_key_or_details(ready: Ready) -> None:
    serve_spools(ready.client, status=500)
    html = calc(ready).text
    assert DE["bambuddy.error.server_error"] in html
    assert "Traceback" not in html


# --- Texts -----------------------------------------------------------------------------------


def test_all_texts_exist_in_german_and_english() -> None:
    keys = {key for key in DE if key.startswith("calc.")}
    assert keys and keys == {key for key in EN if key.startswith("calc.")}


def test_the_template_does_not_switch_off_escaping() -> None:
    source = (ROOT / "app" / "templates" / "calculation_new.html").read_text(encoding="utf-8")
    assert "|safe" not in source.replace(" ", "")
    assert "autoescape" not in source


def test_the_navigation_links_the_calculation_list(ready: Ready) -> None:
    html = ready.client.get("/").text
    assert re.search(r'<a\b[^>]*href="/calculation"[^>]*>\s*' + DE["nav.calculation"], html)


# --- Found by the review -----------------------------------------------------------------------


def test_odd_digits_in_the_article_or_the_form_give_no_server_error(ready: Ready) -> None:
    assert ready.client.get("/calculation/new?article=²").status_code == 404
    response = calc(ready, printer="²", pieces="²", rows="²")
    assert response.status_code == 200
    assert out(response.text, "res-suggested") is None


def test_enter_in_a_field_calculates_and_does_not_add_a_position(ready: Ready) -> None:
    html = ready.client.get("/calculation/new").text
    form = html[html.index('id="calc-form"') :]
    first_button = re.search(r'<button\b[^>]*type="submit"[^>]*>', form).group(0)
    assert 'value="calc"' in first_button


def test_a_spool_with_a_price_of_zero_or_no_weight_left_is_not_used(client: AppClient) -> None:
    ready = make_ready(client)
    serve_spools(client, [spool(1, "PLA", 0), spool(2, "PLA", 20, label=100, used=100)])
    assert select_values(calc(ready).text, "spool_0_0") == ["", "1"]
    html = calc(ready, spool_0_0="1").text
    assert field_error(html, "spool_0_0") == DE["calc.error.spool_price"]
