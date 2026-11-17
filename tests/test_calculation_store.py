"""Saving and managing calculations (issue #161, part b). All data is invented; Bambuddy
answers come from a mock. The test case numbers refer to the comment on issue #161 (part b).
"""

import json
import re
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import AppSettings, Article, Calculation, CalculationMaterial, ChangeLogEntry
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_settings import TEST_SECRET_KEY, answer_with
from tests.test_calculation_page import (
    Ready,
    field_error,
    input_value,
    make_ready,
    out,
    select_values,
    serve_spools,
    spool,
    values,
)
from tests.test_stock import add_article, db

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal
POPUP = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def ready(client: AppClient) -> Ready:
    return make_ready(client)


def save(ready: Ready, article_id: int | None, client: AppClient | None = None, **over: str):
    data = {**values(ready, **over), "action": "save"}
    if article_id is not None:
        data["article"] = str(article_id)
    return (client or ready.client).post("/calculation/new", data=data, follow_redirects=False)


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


def spools_of(calculation: Calculation) -> list:
    return [s for m in calculation.materials for s in m.spools]


def base(number: str = "3D-0001") -> int:
    return add_article(number)


def address(article_id: int, calculation_id: int, tail: str = "") -> str:
    """A saved calculation lives under its own number; its article follows from it (#186)."""
    return f"/calculation/{calculation_id}{tail}"


def saved_one(ready: Ready, article_id: int, **over: str) -> int:
    response = save(ready, article_id, **over)
    assert response.status_code == 303, response.text
    return stored()[-1].id


@pytest.fixture
def employee(ready: Ready) -> AppClient:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME, app=ready.client.app) as other:
        yield other


@pytest.fixture
def reader(ready: Ready) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME, app=ready.client.app) as other:
        yield other


# --- Cases 1-2: when saving is possible --------------------------------------------------------


def test_1_the_form_of_an_article_offers_saving_a_quick_calculation_does_not(
    ready: Ready,
) -> None:
    article_id = base()
    with_article = ready.client.get(f"/calculation/new?article={article_id}").text
    without = ready.client.get("/calculation/new").text
    assert 'value="save"' in with_article
    assert 'value="save"' not in without


def test_1_saving_without_an_article_is_refused_even_when_sent_by_hand(ready: Ready) -> None:
    response = save(ready, None)
    assert response.status_code in (200, 422)
    assert stored() == []
    assert field_error(response.text, "article") == DE["calc.error.article"]


def test_1_saving_on_an_unknown_article_gives_404(ready: Ready) -> None:
    assert save(ready, 999).status_code == 404
    assert stored() == []


def test_2_only_a_complete_calculation_is_saved(ready: Ready) -> None:
    article_id = base()
    for over in ({"spool_0_0": ""}, {"grams_0": "x"}, {"print_minutes": "0"}, {"printer": ""}):
        response = save(ready, article_id, **over)
        assert response.status_code == 422, over
        assert DE["calc.error.save_incomplete"] in response.text
    assert stored() == []


def test_2_a_refused_save_keeps_what_was_typed(ready: Ready) -> None:
    article_id = base()
    html = save(ready, article_id, spool_0_0="", grams_0="77").text
    assert input_value(html, "grams_0") == "77"


# --- Cases 3-4: what is stored ----------------------------------------------------------------


def test_3_everything_is_stored_as_values(ready: Ready) -> None:
    article_id = base()
    response = save(ready, article_id)
    assert response.status_code == 303
    assert response.headers["location"] == "/calculation?saved=1"

    (item,) = stored()
    assert item.article_id == article_id
    assert item.kind == "hand"
    assert item.created_on == date.today()
    assert item.printer_id == ready.printer
    assert item.printer_name == "Testdrucker"
    assert (item.print_minutes, item.pieces) == (D(60), 2)
    assert (item.scrap_percent, item.margin_percent) == (D(5), D(30))
    assert (item.setup_minutes, item.rework_minutes) == (D(0), D(0))
    assert (item.power_price, item.machine_rate, item.labour_rate) == (D("0.30"), D(12), D(15))
    assert item.rounding == "ends_90"
    (material,) = item.materials
    assert (material.material, material.grams, material.minutes) == ("PLA", D(100), D(60))
    assert material.kwh_per_hour == D("0.10")
    (piece,) = spools_of(item)
    assert (piece.spool_id, piece.grams, piece.price_per_kg) == (1, D(100), D(20))
    assert "PLA" in piece.label
    assert item.cost_without_labour == D("7.36575")
    assert item.cost_with_labour == D("7.36575")
    assert item.suggested_price == D("9.90")
    assert item.is_main is True


def test_3_two_spools_are_stored_with_their_grams_and_prices(ready: Ready) -> None:
    save(ready, base(), spool_0_0="5", spool_0_1="2")
    (item,) = stored()
    assert [(s.spool_id, s.grams, s.price_per_kg) for s in spools_of(item)] == [
        (5, D(60), D(20)),
        (2, D(40), D(30)),
    ]


def test_3_the_optional_times_and_the_changed_surcharges_are_stored(ready: Ready) -> None:
    save(ready, base(), setup_minutes="6", rework_minutes="3", scrap="0", margin="10")
    (item,) = stored()
    assert (item.setup_minutes, item.rework_minutes) == (D(6), D(3))
    assert (item.scrap_percent, item.margin_percent) == (D(0), D(10))
    assert item.labour_cost == D("1.50")


def test_4_later_changes_do_not_change_a_saved_calculation(ready: Ready) -> None:
    article_id = base()
    calculation_id = saved_one(ready, article_id)
    with db() as session, session.begin():
        session.get(AppSettings, 1).electricity_price = D("5.00")
        session.get(AppSettings, 1).labour_rate = D("99.00")
    serve_spools(ready.client, [spool(1, "PLA", 99)])

    html = ready.client.get(address(article_id, calculation_id)).text

    assert "7,37" in html and "9,90" in html
    item = stored()[0]
    assert item.power_price == D("0.30") and item.labour_rate == D(15)
    assert spools_of(item)[0].price_per_kg == D(20)


def test_4_viewing_needs_no_bambuddy(ready: Ready) -> None:
    article_id = base()
    calculation_id = saved_one(ready, article_id)

    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("gone")

    answer_with(ready.client, broken)
    response = ready.client.get(address(article_id, calculation_id))
    assert response.status_code == 200
    assert "7,37" in response.text


# --- Cases 5-8: list, view, main ---------------------------------------------------------------


def test_5_the_tile_counts_and_the_table_shows_the_calculations(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    second = saved_one(ready, article_id, grams_0="50")

    detail = ready.client.get(f"/articles/{article_id}").text
    tile = re.search(r'<a\b[^>]*href="/articles/\d+/calculations"[^>]*>.*?</a>', detail, re.S)
    assert tile and ">2<" in re.sub(r"\s+", "", tile.group(0))

    html = ready.client.get(f"/calculation?article={article_id}&saved=1").text
    assert DE["calc.notice.saved"] in html
    assert html.count('<tr class="calc-row') == 2
    assert "Testdrucker" in html
    assert DE["calc.chip.main"] in html
    # In a filtered list the actions keep the filter
    for tail, kind in (("", "view"), ("/edit?filter=1", "edit"), ("/delete?filter=1", "delete")):
        link = address(article_id, first, tail.replace("filter=1", f"filter={article_id}"))
        assert f'hx-get="{link}"' in html, kind
    assert f'hx-get="/calculation/new?copy={first}&amp;filter={article_id}"' in html
    # The main one needs no "make main" button, the other one has it
    assert f'action="{address(article_id, first, "/main")}' not in html
    assert f'action="{address(article_id, second, "/main")}?filter={article_id}"' in html
    assert "data-confirm" not in html and "confirm(" not in html


def test_5_an_empty_list_has_a_hint_and_the_new_button(ready: Ready) -> None:
    article_id = base()
    html = ready.client.get(f"/calculation?article={article_id}").text
    assert DE["calc.list.empty"] in html
    assert f'hx-get="/calculation/start?article={article_id}"' in html


def test_5_the_columns_can_be_sorted(ready: Ready) -> None:
    article_id = base()
    saved_one(ready, article_id)
    saved_one(ready, article_id, grams_0="50")
    for key in ("date", "printer", "cost", "price"):
        for direction in ("asc", "desc"):
            response = ready.client.get(
                f"/calculation?article={article_id}&sort={key}&dir={direction}"
            )
            assert response.status_code == 200
            assert response.text.count('<tr class="calc-row') == 2


def test_6_the_view_shows_all_stored_values(ready: Ready) -> None:
    article_id = base()
    calculation_id = save_with_labour(ready, article_id)
    html = ready.client.get(address(article_id, calculation_id)).text
    for text in ("PLA", "Beispielmarke", "100", "20", "Testdrucker", "11,90", "8,87", "45,34"):
        assert text in html, text
    assert DE["calc.chip.main"] in html


def save_with_labour(ready: Ready, article_id: int) -> int:
    return saved_one(ready, article_id, setup_minutes="6", rework_minutes="3")


def test_7_the_first_calculation_is_main_the_next_ones_are_not(ready: Ready) -> None:
    article_id = base()
    saved_one(ready, article_id)
    saved_one(ready, article_id, grams_0="50")
    assert [c.is_main for c in stored()] == [True, False]


def test_7_main_moves_to_the_chosen_calculation(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    second = saved_one(ready, article_id, grams_0="50")

    response = ready.client.post(address(article_id, second, "/main"), follow_redirects=False)

    assert response.status_code == 303
    assert [(c.id, c.is_main) for c in stored()] == [(first, False), (second, True)]


def test_7_deleting_the_main_calculation_leaves_none_main(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    saved_one(ready, article_id, grams_0="50")

    ready.client.post(address(article_id, first, "/delete"), follow_redirects=False)

    assert [c.is_main for c in stored()] == [False]


def test_8_every_article_and_variant_has_its_own_calculations(ready: Ready) -> None:
    one, two = base("3D-0001"), base("3D-0002")
    saved_one(ready, one)
    other = saved_one(ready, two)
    assert [c.is_main for c in stored()] == [True, True]
    html = ready.client.get(f"/calculation?article={one}").text
    assert html.count('<tr class="calc-row') == 1
    assert address(one, other) not in html and "3D-0002" not in html
    both = ready.client.get("/calculation").text
    assert both.count('<tr class="calc-row') == 2
    assert "3D-0001" in both and "3D-0002" in both


# --- Cases 9-10: copy and edit ----------------------------------------------------------------


def test_9_copy_opens_the_form_with_the_values_filled_in(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id, setup_minutes="6", spool_0_0="5", spool_0_1="2")
    html = ready.client.get(f"/calculation/new?copy={first}").text
    assert input_value(html, "print_minutes") == "60"
    assert 'name="minutes_0"' not in html  # one material: the print time counts
    assert input_value(html, "pieces") == "2"
    assert input_value(html, "grams_0") == "100"
    assert 'name="minutes_0"' not in html  # one material: the print time counts
    assert input_value(html, "setup_minutes") == "6"
    assert re.search(r'<option value="PLA" selected', html)
    assert re.search(r'<option value="5" selected', html)
    assert re.search(r'<option value="2" selected', html)
    assert re.search(rf'<option value="{ready.printer}" selected', html)
    # The current price from Bambuddy is calculated again
    assert out(html, "res-material") == "2,40"


def test_9_saving_a_copy_makes_a_new_calculation_and_keeps_the_old_one(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    before = stored()[0].cost_with_labour

    save(ready, article_id, grams_0="50")

    items = stored()
    assert len(items) == 2
    assert items[0].id == first and items[0].cost_with_labour == before
    assert items[1].is_main is False
    assert items[1].materials[0].grams == D(50)


def test_9_a_spool_that_is_gone_makes_the_copy_incomplete(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    serve_spools(ready.client, [spool(4, "PETG", 25)])
    html = ready.client.get(f"/calculation/new?copy={first}").text
    assert DE["calc.spool.none_for_material"].replace("{material}", "PLA") in html
    assert out(html, "res-suggested") is None


def test_9_a_copy_of_an_unknown_calculation_gives_404(ready: Ready) -> None:
    base()
    for copy in ("999", "x", "0", "99999999999"):
        assert ready.client.get(f"/calculation/new?copy={copy}").status_code == 404, copy


def test_10_edit_shows_the_form_and_saves_over_the_calculation(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    second = saved_one(ready, article_id, grams_0="50")
    before = stored()[1]

    page = ready.client.get(address(article_id, second, "/edit"))
    assert page.status_code == 200
    assert input_value(page.text, "grams_0") == "50"
    assert f'action="{address(article_id, second, "/edit")}"' in page.text

    response = ready.client.post(
        address(article_id, second, "/edit"),
        data={**values(ready, grams_0="80"), "action": "save"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    items = stored()
    assert [c.id for c in items] == [first, second]
    changed = items[1]
    assert changed.materials[0].grams == D(80)
    assert len(changed.materials) == 1 and len(spools_of(changed)) == 1
    assert changed.created_on == before.created_on and changed.is_main is False
    assert changed.cost_with_labour != before.cost_with_labour
    assert items[0].materials[0].grams == D(100)


def test_10_a_refused_edit_changes_nothing(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    response = ready.client.post(
        address(article_id, first, "/edit"),
        data={**values(ready, spool_0_0=""), "action": "save"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert stored()[0].materials[0].grams == D(100)


def test_10_the_live_calculation_works_on_the_edit_page(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    response = ready.client.post(
        address(article_id, first, "/edit"), data=values(ready, grams_0="50")
    )
    assert response.status_code == 200
    assert out(response.text, "res-material") == "1,00"
    assert stored()[0].materials[0].grams == D(100)


# --- Cases 11-13: delete, article delete, change log -------------------------------------------


def test_11_the_delete_question_is_a_popup_with_name_and_two_buttons(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    html = ready.client.get(address(article_id, first, "/delete"), headers=POPUP).text
    assert DE["calc.delete.button"] in html
    assert DE["article.cancel"] in html
    assert date.today().strftime("%d.%m.%Y") in html
    assert "3D-0001" in html
    assert "data-confirm" not in html and "confirm(" not in html
    # Without JavaScript the same question is a page of its own
    assert "<!doctype html>" in ready.client.get(address(article_id, first, "/delete")).text.lower()


def test_11_deleting_removes_only_the_calculation(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    saved_one(ready, article_id, grams_0="50")

    response = ready.client.post(
        address(article_id, first, "/delete"), headers=POPUP, follow_redirects=False
    )

    assert response.status_code == 204
    assert [c.id for c in stored()] != [first] and len(stored()) == 1
    with db() as session:
        assert session.get(Article, article_id) is not None


def test_11_deleting_without_a_popup_goes_back_to_the_list(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    response = ready.client.post(address(article_id, first, "/delete"), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/calculation?")
    assert stored() == []


def test_12_deleting_an_article_deletes_its_calculations(ready: Ready) -> None:
    article_id = base()
    saved_one(ready, article_id)
    saved_one(ready, article_id, grams_0="50")
    keep = base("3D-0002")
    saved_one(ready, keep)

    page = ready.client.get(f"/articles/{article_id}/delete").text
    assert DE["article.delete.with_calculations"] in page
    assert f"{DE['article.purge.count.calculations']}: 2" in page

    response = ready.client.post(f"/articles/{article_id}/delete", follow_redirects=False)
    assert response.status_code in (204, 303)
    assert [c.article_id for c in stored()] == [keep]
    with sqlite3.connect(Settings.from_env().database_path) as connection:
        assert connection.execute("SELECT count(*) FROM calculation_materials").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM calculation_spools").fetchone() == (1,)


def test_12_the_purge_counts_the_calculations_in_the_log(ready: Ready) -> None:
    from tests.test_stock import add_movement

    article_id = base()
    saved_one(ready, article_id)
    add_movement(article_id, 5)

    response = ready.client.post(
        f"/articles/{article_id}/purge", data={"number": "3D-0001"}, follow_redirects=False
    )

    assert response.status_code in (204, 303)
    assert stored() == []
    with db() as session:
        entry = session.scalars(
            select(ChangeLogEntry).where(ChangeLogEntry.action == "article.purged")
        ).one()
        assert {"field": "calculations", "old": "1", "new": "0"} in json.loads(entry.details)


def test_13_every_change_is_written_to_the_change_log(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    ready.client.post(
        address(article_id, first, "/edit"), data={**values(ready, grams_0="80"), "action": "save"}
    )
    second = saved_one(ready, article_id, grams_0="50")
    ready.client.post(address(article_id, second, "/main"))
    ready.client.post(address(article_id, second, "/delete"))

    with db() as session:
        entries = list(session.scalars(select(ChangeLogEntry).order_by(ChangeLogEntry.id)))
    actions = [e.action for e in entries if e.action.startswith("calculation.")]
    assert actions == [
        "calculation.created",
        "calculation.updated",
        "calculation.created",
        "calculation.main",
        "calculation.deleted",
    ]
    assert all("3D-0001" in e.subject for e in entries if e.action.startswith("calculation."))
    assert all(e.user_id for e in entries if e.action.startswith("calculation."))
    for action in set(actions):
        assert DE[f"log.action.{action}"] and EN[f"log.action.{action}"]


# --- Cases 14-15: rights, security -------------------------------------------------------------


def test_14_employee_may_do_everything(ready: Ready, employee: AppClient) -> None:
    article_id = base()
    assert save(ready, article_id, employee).status_code == 303
    first = stored()[0].id
    assert employee.get(address(article_id, first, "/edit")).status_code == 200
    assert (
        employee.post(address(article_id, first, "/main"), follow_redirects=False).status_code
        == 303
    )
    assert (
        employee.post(address(article_id, first, "/delete"), follow_redirects=False).status_code
        == 303
    )
    assert stored() == []


def test_14_reader_sees_but_cannot_change(ready: Ready, reader: AppClient) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    saved_one(ready, article_id, grams_0="50")

    html = reader.get(f"/calculation?article={article_id}").text
    assert html.count('<tr class="calc-row') == 2
    assert reader.get(address(article_id, first)).status_code == 200
    for fragment in ("/edit", "/delete", "/main", "copy=", "/calculation/new"):
        assert f"{address(article_id, first)}{fragment}" not in html, fragment
    assert "copy=" not in html and "/calculation/new" not in html
    assert 'value="save"' not in reader.get(f"/calculation/new?article={article_id}").text

    assert save(ready, article_id, reader).status_code == 403
    for tail in ("/edit", "/delete", "/main"):
        assert reader.get(address(article_id, first, tail)).status_code in (403, 405)
        assert reader.post(address(article_id, first, tail)).status_code == 403
    assert len(stored()) == 2 and stored()[0].materials[0].grams == D(100)


def test_14_nothing_works_without_a_login(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    with AppClient(create_app()) as anonymous:
        for path in (
            "/calculation",
            f"/articles/{article_id}/calculations",
            address(article_id, first),
            address(article_id, first, "/edit"),
            address(article_id, first, "/delete"),
        ):
            response = anonymous.get(path, follow_redirects=False)
            assert "/login" in response.headers.get("location", ""), path
        response = anonymous.post(address(article_id, first, "/delete"), follow_redirects=False)
        assert response.status_code in (303, 307, 401, 403)
    assert len(stored()) == 1


def test_15_unknown_calculations_give_404_everywhere(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    for number in (999, 0, 99999999999):
        for tail in ("", "/edit", "/delete"):
            assert ready.client.get(address(article_id, number, tail)).status_code == 404, tail
        for tail in ("/edit", "/delete", "/main"):
            assert ready.client.post(address(article_id, number, tail)).status_code == 404, tail
    # The nested addresses of the first version are gone
    assert ready.client.get(f"/articles/{article_id}/calculations/{first}").status_code == 404
    assert len(stored()) == 1


def test_15_forms_need_the_form_token(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    ready.client.csrf_token = None
    assert ready.client.post(address(article_id, first, "/delete")).status_code == 403
    assert ready.client.post(address(article_id, first, "/main")).status_code == 403
    assert len(stored()) == 1


def test_a_stored_printer_that_is_gone_must_be_chosen_again_on_edit(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    with db() as session, session.begin():
        from app.models import Device

        session.delete(session.get(Device, ready.printer))
    assert stored()[0].printer_id is None and stored()[0].printer_name == "Testdrucker"
    assert ready.client.get(address(article_id, first)).status_code == 200
    page = ready.client.get(address(article_id, first, "/edit"))
    assert page.status_code == 200


def test_texts_exist_in_german_and_english() -> None:
    assert {k for k in DE if k.startswith("calc.")} == {k for k in EN if k.startswith("calc.")}
    for key in ("table.action.copy", "table.action.main"):
        assert DE[key] and EN[key]


# --- Case 16: migration ------------------------------------------------------------------------


def test_16_migration_adds_the_tables_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "calculations.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0024")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO articles (number, number_key, name, active, created_at)"
            " VALUES ('A-1', 'a-1', 'Alt', 1, '2026-01-01 10:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master")}
        assert {"calculations", "calculation_materials", "calculation_spools"} <= tables
        assert connection.execute("SELECT count(*) FROM calculations").fetchone() == (0,)
        assert connection.execute("SELECT name FROM articles").fetchall() == [("Alt",)]
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0024")
    with sqlite3.connect(database) as connection:
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master")}
        assert not tables & {"calculations", "calculation_materials", "calculation_spools"}
        assert connection.execute("SELECT name FROM articles").fetchall() == [("Alt",)]
    run_migrations(engine)


# --- Issue #186: the list of all calculations, forms in the pop-up -----------------------------


def test_186_the_old_article_address_leads_to_the_filtered_list(ready: Ready) -> None:
    article_id = base()
    response = ready.client.get(f"/articles/{article_id}/calculations", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/calculation?article={article_id}"
    with AppClient(create_app()) as anonymous:
        gone = anonymous.get(f"/articles/{article_id}/calculations", follow_redirects=False)
        assert "/login" in gone.headers.get("location", "")


def test_186_the_list_shows_the_article_of_every_row_and_the_new_button(ready: Ready) -> None:
    article_id = base()
    saved_one(ready, article_id)
    html = ready.client.get("/calculation").text
    assert DE["calc.col.article"] in html
    assert f'<a href="/articles/{article_id}">3D-0001' in html
    assert 'hx-get="/calculation/start"' in html
    assert "data-popup-refresh" in html
    # Opened from the list without a filter the actions carry no filter
    assert "filter=" not in html


def test_186_the_filtered_list_names_the_article_and_hides_other_articles(ready: Ready) -> None:
    one, two = base("3D-0001"), base("3D-0002")
    saved_one(ready, one)
    saved_one(ready, two)
    html = ready.client.get(f"/calculation?article={one}").text
    assert "3D-0001" in html and "3D-0002" not in html
    assert ready.client.get("/calculation?article=999").status_code == 404
    assert ready.client.get("/calculation?article=x").status_code == 404


def test_186_the_new_form_is_a_popup_for_htmx_and_a_page_otherwise(ready: Ready) -> None:
    base()
    popup = ready.client.get("/calculation/new", headers=POPUP).text
    assert 'id="calc-form"' in popup and "<html" not in popup
    page = ready.client.get("/calculation/new").text
    assert "<html" in page and 'id="calc-form"' in page


def test_186_the_article_is_chosen_in_the_form(ready: Ready) -> None:
    one, two = base("3D-0001"), base("3D-0002")
    off = base("3D-0003")
    with db() as session, session.begin():
        session.get(Article, off).active = False
    html = ready.client.get(f"/calculation/new?article={two}", headers=POPUP).text
    assert select_values(html, "article") == ["", str(one), str(two)]
    assert re.search(rf'<option value="{two}" selected', html)
    assert "3D-0002 · Kabelhalter" in html


def test_186_saving_needs_the_article_of_the_form(ready: Ready) -> None:
    article_id = base()
    with_article = ready.client.get(f"/calculation/new?article={article_id}", headers=POPUP).text
    assert 'value="save"' in with_article
    assert 'value="save"' not in ready.client.get("/calculation/new", headers=POPUP).text
    assert save(ready, article_id).status_code == 303
    assert stored()[0].article_id == article_id
    assert stored()[0].created_on == date.today()


def test_186_saving_in_a_popup_goes_to_the_list_with_the_notice(ready: Ready) -> None:
    article_id = base()
    response = ready.client.post(
        "/calculation/new",
        data={**values(ready), "article": str(article_id), "action": "save"},
        headers=POPUP,
        follow_redirects=False,
    )
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/calculation?saved=1"
    assert len(stored()) == 1


def test_186_a_filter_stays_after_saving_marking_and_editing(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    other = base("3D-0002")
    data = {
        **values(ready),
        "article": str(article_id),
        "action": "save",
        "filter": str(article_id),
    }
    response = ready.client.post("/calculation/new", data=data, follow_redirects=False)
    assert response.headers["location"] == f"/calculation?article={article_id}&saved=1"
    # A filter of another article does not count
    data = {**data, "article": str(other)}
    response = ready.client.post("/calculation/new", data=data, follow_redirects=False)
    assert response.headers["location"] == "/calculation?saved=1"
    # The "main" button of a filtered list comes back to it
    second = stored()[1].id
    response = ready.client.post(
        address(article_id, second, f"/main?filter={article_id}"), follow_redirects=False
    )
    assert response.headers["location"] == f"/calculation?article={article_id}&main=1"
    edit = ready.client.post(
        address(article_id, first, f"/edit?filter={article_id}"),
        data={**values(ready), "action": "save"},
        follow_redirects=False,
    )
    assert edit.headers["location"] == f"/calculation?article={article_id}&saved=1"


def test_186_a_made_up_filter_is_not_trusted(ready: Ready) -> None:
    article_id = base()
    for bad in ("//example.com", "x", "999", "-1"):
        data = {**values(ready), "article": str(article_id), "action": "save", "filter": bad}
        response = ready.client.post("/calculation/new", data=data, follow_redirects=False)
        assert response.headers["location"] == "/calculation?saved=1", bad


def test_186_view_edit_and_delete_open_in_the_popup(ready: Ready) -> None:
    article_id = base()
    first = saved_one(ready, article_id)
    for tail in ("", "/edit", "/delete"):
        popup = ready.client.get(address(article_id, first, tail), headers=POPUP).text
        assert "<html" not in popup, tail
        page = ready.client.get(address(article_id, first, tail)).text
        assert "<html" in page, tail
    view = ready.client.get(address(article_id, first), headers=POPUP).text
    assert "7,37" in view and "3D-0001" in view


def test_186_the_article_cannot_be_changed_when_editing(ready: Ready) -> None:
    one, two = base("3D-0001"), base("3D-0002")
    first = saved_one(ready, one)
    page = ready.client.get(address(one, first, "/edit"), headers=POPUP).text
    assert not re.search(r'<select\b[^>]*name="article"', page)
    assert re.search(rf'<input type="hidden" name="article" value="{one}"', page)
    ready.client.post(
        address(one, first, "/edit"),
        data={**values(ready, grams_0="80"), "article": str(two), "action": "save"},
    )
    assert stored()[0].article_id == one


def test_186_the_save_error_stays_in_the_popup(ready: Ready) -> None:
    article_id = base()
    response = ready.client.post(
        "/calculation/new",
        data={**values(ready, spool_0_0=""), "article": str(article_id), "action": "save"},
        headers=POPUP,
    )
    assert response.status_code == 422
    assert "<html" not in response.text
    assert DE["calc.error.save_incomplete"] in response.text
