"""Calculation with filaments and price rules (issue #237). All data is invented.

The test case numbers refer to the confirmed list in the issue. The browser test is in
tests/browser/test_calculation_filament.py. Bambuddy answers come from a mock.
"""

import json
import re
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import filaments
from app.db import alembic_config, make_engine, run_migrations
from app.models import (
    AppSettings,
    Calculation,
    CalculationMaterial,
    Filament,
    SpoolAssignment,
)
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.forms import valid_form
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_calculation_page import (
    Ready,
    calc,
    field_error,
    make_ready,
    out,
    select_values,
    serve_spools,
    spool,
    values,
)
from tests.test_calculation_store import address, base, save, saved_one
from tests.test_stock import db

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 1, 12, 0, 0)
# The material cost of 100 g at the price of this spool is the reference for the rules
SPOOLS = [
    spool(1, "PLA", 20),
    spool(2, "PLA", 30),
    spool(6, "PLA", 27.5),
    spool(7, "PLA", 25),
    spool(8, "PLA", 18),
    spool(9, "PLA", 30),
]


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def ready(client: AppClient) -> Ready:
    made = make_ready(client)
    serve_spools(client, SPOOLS)
    return made


def add_filament(
    color: str = "Ivory White", material: str = "PLA", standard: str | None = None, active=True
) -> int:
    with db() as session, session.begin():
        item = Filament(
            brand="Bambu Lab",
            material=material,
            subtype="Matte",
            color_name=color,
            color="",
            standard_price=D(standard) if standard is not None else None,
            note="",
            created_at=NOW,
            active=active,
            identity_key=filaments.identity_key("Bambu Lab", material, "Matte", color),
        )
        session.add(item)
        session.flush()
        return item.id


def assign(
    filament_id: int,
    spool_id: int,
    price: str | None,
    weight: str | None = "1000",
    minutes: int = 0,
) -> None:
    with db() as session, session.begin():
        session.add(
            SpoolAssignment(
                spool_id=spool_id,
                filament_id=filament_id,
                price_per_kg=D(price) if price is not None else None,
                weight_g=D(weight) if weight is not None else None,
                spool_key="k",
                assigned_at=NOW + timedelta(minutes=minutes),
            )
        )


def with_rule(filament_id: int, rule: str, **over: str) -> dict[str, str]:
    """The form fields of a position that uses a filament with a rule (no spool chosen)."""
    return {"rule_0": rule, "filament_0": str(filament_id), "spool_0_0": "", **over}


def material_cost(ready: Ready, spool_id: int) -> str:
    """What 100 g cost when taken from the Bambuddy spool: the reference."""
    return out(calc(ready, spool_0_0=str(spool_id)).text, "res-material")


def main_filament(ready: Ready) -> int:
    """The filament of the rule cases: 1000 g at 20 and 3000 g at 30 EUR/kg (assigned in this
    order, a later one for 18 stays out), standard price 25."""
    fid = add_filament(standard="25")
    assign(fid, 1, "20", "1000", minutes=0)
    assign(fid, 2, "30", "3000", minutes=5)
    return fid


def stored() -> list[Calculation]:
    with db() as session:
        return list(
            session.scalars(
                select(Calculation)
                .options(
                    selectinload(Calculation.materials).selectinload(CalculationMaterial.spools)
                )
                .order_by(Calculation.id)
            )
        )


def set_default_rule(rule: str) -> None:
    with db() as session, session.begin():
        session.get(AppSettings, 1).price_rule = rule


# --- Case 1-3: the choice in the form --------------------------------------------------------


def test_1_every_position_has_the_rule_choice_and_the_default_is_single_spool(
    ready: Ready,
) -> None:
    html = calc(ready).text
    assert select_values(html, "rule_0") == ["average", "last", "standard", "spool"]
    assert re.search(r'<option value="spool" selected', html)
    assert out(html, "res-suggested") == "9,90"  # unchanged from before


def test_1_the_default_rule_comes_from_the_settings(ready: Ready) -> None:
    set_default_rule("average")
    html = ready.client.post(
        "/calculation/new", data={k: v for k, v in values(ready).items() if k != "rule_0"}
    ).text
    assert re.search(r'<option value="average" selected', html)


def test_2_a_filament_rule_shows_the_filament_choice_instead_of_the_spools(
    ready: Ready,
) -> None:
    fid = add_filament()
    add_filament("Black", material="PETG")
    add_filament("Red", active=False)
    html = calc(ready, **with_rule(fid, "average")).text
    assert select_values(html, "filament_0") == ["", str(fid)]
    assert 'name="spool_0_0"' not in html


def test_2_single_spool_keeps_the_spool_choice(ready: Ready) -> None:
    add_filament()
    html = calc(ready).text
    assert 'name="spool_0_0"' in html
    assert 'name="filament_0"' not in html


def test_3_without_a_matching_filament_there_is_a_hint_and_no_result(ready: Ready) -> None:
    add_filament("Black", material="PETG")
    html = calc(ready, rule_0="average", filament_0="", spool_0_0="").text
    assert DE["calc.filament.none_for_material"].replace("{material}", "PLA") in html
    assert out(html, "res-suggested") is None


# --- Case 4-10: the price by rule ------------------------------------------------------------


def test_4_average_is_weighted_by_grams(ready: Ready) -> None:
    fid = main_filament(ready)
    assert out(calc(ready, **with_rule(fid, "average")).text, "res-material") == material_cost(
        ready, 6
    )


def test_5_last_price_is_the_one_assigned_last(ready: Ready) -> None:
    fid = main_filament(ready)
    assign(fid, 8, "18", minutes=10)
    assert out(calc(ready, **with_rule(fid, "last")).text, "res-material") == material_cost(
        ready, 8
    )


def test_6_standard_price_is_the_one_of_the_filament(ready: Ready) -> None:
    fid = main_filament(ready)
    assert out(calc(ready, **with_rule(fid, "standard")).text, "res-material") == material_cost(
        ready, 7
    )


def test_7_archived_and_deleted_spools_count_and_bambuddy_is_not_asked(ready: Ready) -> None:
    fid = add_filament()
    assign(fid, 98, "20")  # not in Bambuddy any more
    assign(fid, 99, "30", minutes=5)
    serve_spools(ready.client, [], status=500)  # Bambuddy does not answer at all
    html = calc(ready, **with_rule(fid, "average")).text
    assert out(html, "res-material") is not None
    serve_spools(ready.client, SPOOLS)
    assert out(html, "res-material") == material_cost(ready, 7)


def test_8_spools_without_price_never_count_as_zero(ready: Ready) -> None:
    fid = add_filament()
    assign(fid, 1, "20")
    assign(fid, 2, None, minutes=5)
    assert out(calc(ready, **with_rule(fid, "average")).text, "res-material") == material_cost(
        ready, 1
    )


@pytest.mark.parametrize("rule", ["average", "last", "standard"])
def test_9_a_missing_price_leaves_the_position_incomplete(ready: Ready, rule: str) -> None:
    fid = add_filament()  # no standard price and no spool
    html = calc(ready, **with_rule(fid, rule)).text
    assert DE[f"calc.filament.no_price_{rule}"] in html
    assert out(html, "res-suggested") is None
    assert save(ready, base(), **with_rule(fid, rule)).status_code == 422
    assert stored() == []


def test_10_the_need_is_not_checked_against_the_stock(ready: Ready) -> None:
    fid = add_filament(standard="25")  # no spools at all: stock 0
    html = calc(ready, **with_rule(fid, "standard"), grams_0="5000").text
    assert out(html, "res-suggested") is not None


def test_10_no_spools_are_spread_over_for_a_filament_rule(ready: Ready) -> None:
    fid = main_filament(ready)
    assert save(ready, base(), **with_rule(fid, "average")).status_code == 303
    (item,) = stored()
    (line,) = item.materials[0].spools
    assert line.grams == D("100") and line.spool_id is None


# --- Case 11-14: saving, viewing, copying ---------------------------------------------------


def test_11_rule_filament_price_and_grams_are_stored_as_values(ready: Ready) -> None:
    fid = main_filament(ready)
    name = "Bambu Lab · PLA Matte · Ivory White"
    save(ready, base(), **with_rule(fid, "average"))
    (item,) = stored()
    material = item.materials[0]
    assert material.price_rule == "average" and material.filament_id == fid
    (line,) = material.spools
    assert line.label == name
    assert line.price_per_kg == D("27.5") and line.grams == D("100")
    # later changes do not touch it
    with db() as session, session.begin():
        session.query(SpoolAssignment).update({"price_per_kg": D("99")})
        session.get(Filament, fid).standard_price = D("99")
    (again,) = stored()
    assert again.materials[0].spools[0].price_per_kg == D("27.5")
    assert again.material_per_plate == item.material_per_plate


def test_12_the_view_shows_filament_rule_and_price_and_old_ones_stay_as_before(
    ready: Ready,
) -> None:
    fid = main_filament(ready)
    old = saved_one(ready, base("3D-0001"))
    new = saved_one(ready, base("3D-0002"), **with_rule(fid, "average"))
    new_html = ready.client.get(address(0, new)).text
    assert "Bambu Lab · PLA Matte · Ivory White" in new_html
    assert DE["calc.rule.average"] in new_html and "27,50" in new_html
    old_html = ready.client.get(address(0, old)).text
    assert DE["calc.rule.average"] not in old_html and "#1 " in old_html
    assert stored()[0].materials[0].price_rule == "spool"


def test_13_a_copy_keeps_filament_and_rule_but_uses_the_current_prices(ready: Ready) -> None:
    fid = main_filament(ready)
    item_id = saved_one(ready, base(), **with_rule(fid, "standard"))
    with db() as session, session.begin():
        session.get(Filament, fid).standard_price = D("30")
    html = ready.client.get(f"/calculation/new?copy={item_id}").text
    assert re.search(r'<option value="standard" selected', html)
    assert re.search(rf'<option value="{fid}" selected', html)
    assert out(html, "res-material") == material_cost(ready, 9)  # 30 per kg now


def test_14_a_deleted_or_switched_off_filament_leaves_the_calculation_readable(
    ready: Ready,
) -> None:
    fid = add_filament(standard="25")
    item_id = saved_one(ready, base(), **with_rule(fid, "standard"))
    with db() as session, session.begin():
        session.get(Filament, fid).active = False
    html = ready.client.get(address(0, item_id)).text
    assert "Bambu Lab · PLA Matte · Ivory White" in html and "25,00" in html
    copy = ready.client.get(f"/calculation/new?copy={item_id}").text
    assert field_error(copy, "filament_0") == DE["calc.error.filament"]
    with db() as session, session.begin():
        session.delete(session.get(Filament, fid))
    assert ready.client.get(address(0, item_id)).status_code == 200
    copy = ready.client.get(f"/calculation/new?copy={item_id}").text
    assert field_error(copy, "filament_0") == DE["calc.error.filament"]


# --- Case 15-17: settings, rights, production ---------------------------------------------


def test_15_the_default_rule_is_chosen_in_the_settings(client: AppClient) -> None:
    html = client.get("/settings").text
    assert select_values(html, "price_rule") == ["average", "last", "standard", "spool"]
    assert client.post("/settings", data=valid_form(price_rule="last")).status_code in (200, 303)
    with db() as session:
        assert session.get(AppSettings, 1).price_rule == "last"


def test_15_an_unknown_rule_is_refused_in_the_settings(client: AppClient) -> None:
    response = client.post("/settings", data=valid_form(price_rule="cheapest"))
    assert response.status_code == 422
    with db() as session:
        assert session.get(AppSettings, 1).price_rule == "spool"


def test_16_unknown_filament_wrong_material_and_unknown_rule_are_errors(ready: Ready) -> None:
    petg = add_filament("Black", material="PETG")
    assert (
        field_error(calc(ready, **with_rule(999, "average")).text, "filament_0")
        == DE["calc.error.filament"]
    )
    assert (
        field_error(calc(ready, **with_rule(petg, "average")).text, "filament_0")
        == DE["calc.error.filament"]
    )
    fid = add_filament()
    assert (
        field_error(calc(ready, **with_rule(fid, "cheapest")).text, "rule_0")
        == DE["calc.error.rule"]
    )
    assert save(ready, base(), **with_rule(petg, "average")).status_code == 422
    assert stored() == []


def test_16_the_rights_are_as_before(ready: Ready) -> None:
    fid = main_filament(ready)
    add_user(READER_NAME, "read_only")
    add_user(EMPLOYEE_NAME, "employee")
    article = base()
    with client_as(READER_NAME, app=ready.client.app) as reader:
        assert calc(ready, reader, **with_rule(fid, "average")).status_code == 200
        assert save(ready, article, reader, **with_rule(fid, "average")).status_code == 403
    with client_as(EMPLOYEE_NAME, app=ready.client.app) as employee:
        assert save(ready, article, employee, **with_rule(fid, "average")).status_code == 303


def test_17_the_production_page_knows_nothing_about_filament_prices() -> None:
    source = (ROOT / "app" / "production.py").read_text(encoding="utf-8")
    pages = (ROOT / "app" / "production_pages.py").read_text(encoding="utf-8")
    for text in (source, pages):
        for name in ("filament_stock", "price_rule", "FilamentPrices", "filament_prices_of"):
            assert name not in text


# --- Migration and texts ------------------------------------------------------------------


def test_migration_adds_the_columns_keeps_old_rows_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "calculations.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0031")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO articles (id, number, number_key, name, active, created_at)"
            " VALUES (1, 'A-1', 'a-1', 'Alt', 1, '2026-01-01 10:00:00')"
        )
        names = [row[1] for row in connection.execute("PRAGMA table_info(calculations)") if row[3]]
        fill = {
            "id": "1",
            "article_id": "1",
            "created_on": "2026-01-01",
            "created_at": "2026-01-01 10:00:00",
            "printer_name": "P1",
            "pieces": "1",
            "rounding": "none",
            "kind": "hand",
        }
        marks = ", ".join(f"'{fill.get(name, '1')}'" for name in names)
        columns = ", ".join(names)
        connection.execute(f"INSERT INTO calculations ({columns}) VALUES ({marks})")  # noqa: S608
        connection.execute(
            "INSERT INTO calculation_materials (id, calculation_id, position, material, grams,"
            " minutes, kwh_per_hour) VALUES (1, 1, 0, 'PLA', '100', '60', '0.1')"
        )
        connection.execute(
            "INSERT INTO calculation_spools (id, material_id, position, spool_id, label, grams,"
            " price_per_kg) VALUES (1, 1, 0, 5, '#5 PLA', '100', '20')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        rule, filament = connection.execute(
            "SELECT price_rule, filament_id FROM calculation_materials"
        ).fetchone()
        assert (rule, filament) == ("spool", None)
        assert connection.execute("SELECT spool_id FROM calculation_spools").fetchone() == (5,)
        assert connection.execute("SELECT price_rule FROM app_settings").fetchone() == ("spool",)
        info = {
            row[1]: row[3] for row in connection.execute("PRAGMA table_info(calculation_spools)")
        }
        assert info["spool_id"] == 0  # may be empty now
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0031")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(app_settings)")}
        assert "price_rule" not in columns
        assert connection.execute("SELECT spool_id FROM calculation_spools").fetchone() == (5,)
    run_migrations(engine)


def test_all_new_texts_exist_in_german_and_english() -> None:
    keys = [
        "calc.field.rule",
        "calc.field.filament",
        "calc.choose_filament",
        "calc.rule.average",
        "calc.rule.last",
        "calc.rule.standard",
        "calc.rule.spool",
        "calc.filament.none_for_material",
        "calc.filament.no_price_average",
        "calc.filament.no_price_last",
        "calc.filament.no_price_standard",
        "calc.error.filament",
        "calc.error.rule",
        "settings.field.price_rule",
        "settings.hint.price_rule",
    ]
    for key in keys:
        assert DE.get(key) and EN.get(key), key
