"""Page Production, part 2: creating, booking and cancelling productions (issue #127, cases 1-21).

All data is invented; Bambuddy answers come from a mock. The test case numbers refer to the
comment on the issue.
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.main import create_app
from app.models import (
    ChangeLogEntry,
    Production,
    StockMovement,
)
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_page import names, prepared, raw, rows
from tests.test_stock import add_article, add_movement, db, stock

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal

# Three prints with known costs: 3.00 + 0.50 + 1.00, 2.00 + 0.25 + 0.50, a failed one with
# 1.00 + 0.10 + 0.20, and one that is still running.
ARCHIVES = [
    raw(9, "2026-11-15T12:00:00", status="printing", name="Laufender Druck"),
    raw(
        8, "2026-11-15T11:00:00", status="failed", name="Fehldruck", cost=1.0, energy=0.1, wear=0.2
    ),
    raw(7, "2026-11-15T10:00:00", name="Druck B", cost=2.0, energy=0.25, wear=0.5),
    raw(6, "2026-11-15T09:00:00", name="Druck A", cost=3.0, energy=0.5, wear=1.0),
    raw(5, "2026-11-14T09:00:00", name="Druck ohne Kosten", cost=None, energy=None, wear=None),
]


def setup_prints(client: AppClient) -> None:
    prepared(client, ARCHIVES)


def form(ids: list[int], results: list[tuple[int, str] | tuple[int, str, str]], **extra: object):
    """The data of the booking form (what a browser sends)."""
    data: dict[str, object] = {
        "prints": [str(i) for i in ids],
        "rows": str(max(len(results), 3)),
        "date": "",
        "note": "",
        "action": "book",
    }
    for number, result in enumerate(results, start=1):
        data[f"article_{number}"] = str(result[0])
        data[f"quantity_{number}"] = result[1]
        if len(result) == 3:
            data[f"weight_{number}"] = result[2]
    data.update(extra)
    return data


def send(client: AppClient, data: dict[str, object]):
    return client.post("/production/new", data=data, follow_redirects=False)


def productions() -> list[Production]:
    with db() as session:
        found = list(session.scalars(select(Production).order_by(Production.id)))
        for item in found:
            len(item.prints), len(item.results)
        session.expunge_all()
        return found


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def prepared_client(client: AppClient) -> AppClient:
    setup_prints(client)
    return client


# --- Cases 1-2: rights and form token ----------------------------------------------------------


def test_reader_sees_productions_but_gets_no_buttons_and_no_booking(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    send(prepared_client, form([6], [(article_id, "2")]))
    add_user(READER_NAME, "read_only")

    with client_as(READER_NAME) as reader:
        reader.app.state.bambuddy_transport = prepared_client.app.state.bambuddy_transport
        assert 'name="prints"' not in reader.get("/production").text
        assert reader.get("/production/list").status_code == 200
        assert reader.get("/production/1").status_code == 200
        assert DE["production.cancel"] not in reader.get("/production/list").text
        assert reader.get("/production/new?prints=7").status_code == 403
        assert send(reader, form([7], [(article_id, "1")])).status_code == 403
        assert reader.get("/production/1/cancel").status_code == 403
        assert reader.post("/production/1/cancel", data={}).status_code == 403
    assert len(productions()) == 1


def test_employee_may_book_and_cancel(prepared_client: AppClient) -> None:
    article_id = add_article()
    add_user(EMPLOYEE_NAME, "employee")

    with client_as(EMPLOYEE_NAME) as employee:
        employee.app.state.bambuddy_transport = prepared_client.app.state.bambuddy_transport
        assert send(employee, form([6], [(article_id, "2")])).status_code == 303
        assert (
            employee.post("/production/1/cancel", data={}, follow_redirects=False).status_code
            == 303
        )


def test_without_login_everything_leads_to_the_login(prepared_client: AppClient) -> None:
    with AppClient(create_app()) as anonymous:
        for path in ("/production/list", "/production/new?prints=6", "/production/1"):
            response = anonymous.get(path, follow_redirects=False)
            assert response.status_code in (302, 303)
            assert "/login" in response.headers["location"]


@pytest.mark.parametrize("path", ["/production/new", "/production/1/cancel"])
def test_post_without_a_valid_form_token_is_refused(prepared_client: AppClient, path: str) -> None:
    article_id = add_article()
    send(prepared_client, form([6], [(article_id, "2")]))
    prepared_client.csrf_token = None

    for token in ({}, {"csrf_token": "falsch"}):
        response = prepared_client.post(path, data={**form([7], [(article_id, "1")]), **token})
        assert response.status_code == 403
    assert len(productions()) == 1


# --- Case 3: choosing prints -------------------------------------------------------------------


def test_only_finished_failed_or_cancelled_prints_can_be_chosen(
    prepared_client: AppClient,
) -> None:
    html = prepared_client.get("/production").text

    by_name = dict(zip(names(html), rows(html), strict=True))
    assert 'name="prints"' in by_name["Druck A"]
    assert 'name="prints"' in by_name["Fehldruck"]
    assert 'name="prints"' not in by_name["Laufender Druck"]
    assert DE["production.create"] in html
    assert 'action="/production/new"' in html and 'method="get"' in html


def test_creating_without_prints_gives_a_hint(prepared_client: AppClient) -> None:
    response = prepared_client.get("/production/new")

    assert response.status_code == 422
    assert DE["production.error.no_print"] in response.text


def test_a_running_or_unknown_print_cannot_be_used(prepared_client: AppClient) -> None:
    assert prepared_client.get("/production/new?prints=9").status_code == 422
    assert prepared_client.get("/production/new?prints=999").status_code == 422
    article_id = add_article()
    assert send(prepared_client, form([9], [(article_id, "1")])).status_code == 422
    assert productions() == []


# --- Cases 4-5: failed prints, prints that are taken -------------------------------------------


def test_a_failed_print_can_be_booked_and_its_costs_count(prepared_client: AppClient) -> None:
    article_id = add_article()

    response = send(prepared_client, form([6, 8], [(article_id, "5")]))

    assert response.status_code == 303
    (item,) = productions()
    assert item.total_cost == D("5.80")
    assert {p.archive_id: p.status for p in item.prints} == {6: "success", 8: "failed"}


def test_a_print_that_was_taken_meanwhile_is_refused(prepared_client: AppClient) -> None:
    article_id = add_article()
    send(prepared_client, form([6], [(article_id, "1")]))

    response = send(prepared_client, form([6, 7], [(article_id, "1")]))

    assert response.status_code == 422
    assert DE["production.error.print_taken"] in response.text
    assert len(productions()) == 1


# --- Cases 6-7: costs and preview --------------------------------------------------------------


def test_the_form_has_the_costs_from_bambuddy_and_a_hint_for_missing_ones(
    prepared_client: AppClient,
) -> None:
    html = prepared_client.get("/production/new?prints=6&prints=5").text

    assert re.search(r'name="cost_material_6"[^>]*value="3"', html)
    assert re.search(r'name="cost_electricity_6"[^>]*value="0,5"', html)
    assert re.search(r'name="cost_wear_6"[^>]*value="1"', html)
    assert re.search(r'name="cost_material_5"[^>]*value=""', html)
    assert DE["production.cost_missing"] in html
    assert html.count(DE["production.cost_missing"]) == 1
    assert "Druck A" in html and "Druck ohne Kosten" in html


def test_missing_costs_count_as_zero_and_edited_costs_win(prepared_client: AppClient) -> None:
    article_id = add_article()
    data = form([5, 6], [(article_id, "2")], cost_material_6="4,50", cost_wear_6="")

    send(prepared_client, data)

    (item,) = productions()
    assert item.total_cost == D("5.00")
    assert sorted(p.material_cost for p in item.prints) == [D("0"), D("4.50")]


def test_the_preview_shows_total_split_and_cost_per_piece(prepared_client: AppClient) -> None:
    first, second = add_article("A-1"), add_article("A-2")

    response = send(
        prepared_client,
        form([6, 7], [(first, "6"), (second, "4")], action="preview"),
    )

    assert response.status_code == 200
    html = response.text
    assert DE["production.preview.total"] in html
    assert "7,25" in html
    assert re.search(r'name="alloc_1"[^>]*value="4,35"', html)
    assert re.search(r'name="alloc_2"[^>]*value="2,90"', html)
    assert "0,725" in html or "0,7250" in html
    assert 'name="preview_key"' in html
    assert productions() == [] and count_movements() == 0


def test_a_single_article_gets_everything_in_the_preview(prepared_client: AppClient) -> None:
    article_id = add_article()

    html = send(prepared_client, form([6, 7], [(article_id, "5")], action="preview")).text

    assert re.search(r'name="alloc_1"[^>]*value="7,25"', html)
    assert "1,45" in html


def test_weights_count_in_the_preview(prepared_client: AppClient) -> None:
    holder, clip = add_article("H-1"), add_article("C-1")

    html = send(
        prepared_client,
        form([6], [(holder, "2", "3"), (clip, "2", "1")], action="preview"),
    ).text

    # 4.50 split 6 : 2 is 3.375 and 1.125; the cent that is left over goes to the larger share.
    assert re.search(r'name="alloc_1"[^>]*value="3,37"', html)
    assert re.search(r'name="alloc_2"[^>]*value="1,13"', html)


def count_movements() -> int:
    with db() as session:
        return len(session.scalars(select(StockMovement)).all())


# --- Case 8: correcting the split by hand ------------------------------------------------------


def preview_key(client: AppClient, data: dict[str, object]) -> str:
    html = send(client, {**data, "action": "preview"}).text
    return re.search(r'name="preview_key"[^>]*value="([^"]+)"', html).group(1)


def test_a_hand_made_split_is_booked_when_it_adds_up(prepared_client: AppClient) -> None:
    first, second = add_article("A-1"), add_article("A-2")
    data = form([6, 7], [(first, "6"), (second, "4")])
    data["preview_key"] = preview_key(prepared_client, data)

    response = send(prepared_client, {**data, "alloc_1": "5,25", "alloc_2": "2,00"})

    assert response.status_code == 303
    (item,) = productions()
    assert [(r.allocated_cost, r.split) for r in item.results] == [
        (D("5.25"), True),
        (D("2.00"), True),
    ]


def test_a_hand_made_split_that_does_not_add_up_books_nothing(prepared_client: AppClient) -> None:
    first, second = add_article("A-1"), add_article("A-2")
    data = form([6, 7], [(first, "6"), (second, "4")])
    data["preview_key"] = preview_key(prepared_client, data)

    response = send(prepared_client, {**data, "alloc_1": "5,25", "alloc_2": "1,00"})

    assert response.status_code == 422
    assert DE["production.error.alloc_sum"] in response.text
    assert productions() == [] and count_movements() == 0


def test_a_split_from_an_old_preview_is_refused_when_the_form_changed(
    prepared_client: AppClient,
) -> None:
    first, second = add_article("A-1"), add_article("A-2")
    data = form([6, 7], [(first, "6"), (second, "4")])
    key = preview_key(prepared_client, data)
    changed = form([6, 7], [(first, "1"), (second, "9")])

    response = send(
        prepared_client, {**changed, "preview_key": key, "alloc_1": "4,35", "alloc_2": "2,90"}
    )

    assert response.status_code == 422
    assert DE["production.error.preview_stale"] in response.text
    assert productions() == []


@pytest.mark.parametrize("value", ["abc", "-1", "1,23456"])
def test_bad_hand_made_costs_are_refused(prepared_client: AppClient, value: str) -> None:
    first, second = add_article("A-1"), add_article("A-2")
    data = form([6, 7], [(first, "6"), (second, "4")])
    data["preview_key"] = preview_key(prepared_client, data)

    response = send(prepared_client, {**data, "alloc_1": value, "alloc_2": "2,90"})

    assert response.status_code == 422
    assert field_error(response.text, "alloc_1")
    assert productions() == []


def test_bambuddy_costs_with_many_decimals_are_rounded_in_the_form_and_can_be_booked(
    client: AppClient,
) -> None:
    prepared(client, [raw(1, cost=0.123456, energy=0.00005, wear=1.0)])
    article_id = add_article()

    html = client.get("/production/new?prints=1").text

    assert re.search(r'name="cost_material_1"[^>]*value="0,1235"', html)
    assert re.search(r'name="cost_electricity_1"[^>]*value="0,0001"', html)
    # What the form shows can be sent back as it is.
    assert send(client, form([1], [(article_id, "2")])).status_code == 303
    # And without the cost fields the same rounding counts.
    assert productions()[0].prints[0].material_cost == D("0.1235")


# --- Case 9: rounding and bad numbers ----------------------------------------------------------


def test_the_total_is_rounded_to_the_cent(prepared_client: AppClient) -> None:
    article_id = add_article()
    data = form(
        [6], [(article_id, "1")], cost_material_6="0,005", cost_electricity_6="0", cost_wear_6="0"
    )

    assert send(prepared_client, data).status_code == 303

    assert productions()[0].total_cost == D("0.01")


@pytest.mark.parametrize("value", ["1,23456", "-1", "abc", "1e3", "1.2.3", "1" * 12])
def test_bad_costs_are_refused(prepared_client: AppClient, value: str) -> None:
    article_id = add_article()
    data = form([6], [(article_id, "1")], cost_material_6=value)

    response = send(prepared_client, data)

    assert response.status_code == 422
    assert field_error(response.text, "cost_material_6")
    assert productions() == []


# --- Cases 10-11: booking through the page -----------------------------------------------------


def test_booking_raises_the_stock_and_leads_to_the_list_with_a_message(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()

    response = send(prepared_client, form([6, 7], [(article_id, "10")]))

    assert response.status_code == 303
    assert response.headers["location"] == "/production/list?booked=1"
    assert stock(article_id) == 10
    page = prepared_client.get(response.headers["location"]).text
    assert DE["production.booked"] in page


def test_one_article_on_five_plates(prepared_client: AppClient) -> None:
    prepared(prepared_client, [raw(n, f"2026-11-15T0{n}:00:00") for n in range(5, 0, -1)])
    article_id = add_article()

    assert send(prepared_client, form([1, 2, 3, 4, 5], [(article_id, "8")])).status_code == 303

    (item,) = productions()
    assert len(item.prints) == 5 and len(item.results) == 1
    assert stock(article_id) == 8


def test_two_articles_on_one_plate(prepared_client: AppClient) -> None:
    first, second = add_article("A-1"), add_article("A-2")

    assert send(prepared_client, form([6], [(first, "3"), (second, "2")])).status_code == 303

    (item,) = productions()
    assert [r.allocated_cost for r in item.results] == [D("2.70"), D("1.80")]
    assert (stock(first), stock(second)) == (3, 2)


def test_two_articles_on_three_plates(prepared_client: AppClient) -> None:
    first, second = add_article("A-1"), add_article("A-2")

    assert send(prepared_client, form([6, 7, 8], [(first, "4"), (second, "4")])).status_code == 303

    (item,) = productions()
    assert item.total_cost == D("8.55")
    assert sum(r.allocated_cost for r in item.results) == D("8.55")


def test_booked_prints_leave_the_open_view_and_link_to_their_production(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    send(prepared_client, form([6], [(article_id, "1")]))

    assert "Druck A" not in names(prepared_client.get("/production").text)
    assigned = prepared_client.get("/production?view=assigned").text
    assert names(assigned) == ["Druck A"]
    assert 'href="/production/1"' in assigned


# --- Case 12: invalid input --------------------------------------------------------------------


def future() -> str:
    return (date.today() + timedelta(days=2)).isoformat()


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"article_1": ""}, "article_1"),
        ({"article_1": "99999"}, "article_1"),
        ({"quantity_1": "0"}, "quantity_1"),
        ({"quantity_1": "-2"}, "quantity_1"),
        ({"quantity_1": "abc"}, "quantity_1"),
        ({"quantity_1": "1" * 12}, "quantity_1"),
        ({"weight_1": "0"}, "weight_1"),
        ({"weight_1": "x"}, "weight_1"),
        ({"date": "gestern"}, "date"),
        ({"date": "2999-01-01"}, "date"),
        ({"date": "future"}, "date"),
        ({"note": "x" * 501}, "note"),
    ],
)
def test_invalid_input_shows_errors_keeps_the_input_and_books_nothing(
    prepared_client: AppClient, change: dict[str, str], field: str
) -> None:
    article_id = add_article()
    data = form([6], [(article_id, "2")], note="Meine Notiz")
    data.update({k: (future() if v == "future" else v) for k, v in change.items()})

    response = send(prepared_client, data)

    assert response.status_code == 422
    assert field_error(response.text, field)
    assert productions() == [] and count_movements() == 0
    assert "Meine Notiz" in response.text or field == "note"


def test_an_inactive_article_and_the_same_article_twice_are_refused(
    prepared_client: AppClient,
) -> None:
    inactive, active = add_article("X-1", active=False), add_article("A-1")

    inactive_response = send(prepared_client, form([6], [(inactive, "1")]))
    twice = send(prepared_client, form([6], [(active, "1"), (active, "2")]))

    assert inactive_response.status_code == 422 and field_error(inactive_response.text, "article_1")
    assert twice.status_code == 422 and field_error(twice.text, "article_2")
    assert productions() == []


def test_the_input_stays_in_the_form_after_an_error(prepared_client: AppClient) -> None:
    article_id = add_article()
    data = form([6, 7], [(article_id, "2")], quantity_1="0", cost_material_6="4,40", note="Bleibt")

    html = send(prepared_client, data).text

    assert 'value="Bleibt"' in html
    assert re.search(r'name="cost_material_6"[^>]*value="4,40"', html)
    assert re.search(r'name="quantity_1"[^>]*value="0"', html)
    assert html.count('name="prints"') == 2


def test_a_row_can_be_added_and_there_is_an_upper_limit(prepared_client: AppClient) -> None:
    article_id = add_article()
    data = form([6], [(article_id, "2")], action="add_row", rows="3")

    html = send(prepared_client, data).text

    assert html.count('name="article_') == 4
    assert productions() == []
    limit = send(prepared_client, {**data, "rows": "20"})
    assert limit.text.count('name="article_') == 20


# --- Case 13: double submit --------------------------------------------------------------------


def test_sending_twice_books_only_once(prepared_client: AppClient) -> None:
    article_id = add_article()
    data = form([6], [(article_id, "2")])

    first, second = send(prepared_client, data), send(prepared_client, data)

    assert (first.status_code, second.status_code) == (303, 422)
    assert DE["production.error.print_taken"] in second.text
    assert len(productions()) == 1 and stock(article_id) == 2


# --- Case 14, 19: change log -------------------------------------------------------------------


def test_booking_and_cancelling_are_in_the_change_log(prepared_client: AppClient) -> None:
    article_id = add_article()
    send(prepared_client, form([6], [(article_id, "2")]))
    prepared_client.post("/production/1/cancel", data={})

    with db() as session:
        actions = [
            e.action for e in session.scalars(select(ChangeLogEntry).order_by(ChangeLogEntry.id))
        ]
    assert actions[-2:] == ["production.booked", "production.cancelled"]
    log_page = prepared_client.get("/settings/log").text
    assert DE["log.action.production.booked"] in log_page
    assert DE["log.action.production.cancelled"] in log_page


# --- Case 15: list of productions --------------------------------------------------------------


def production_rows(html: str) -> list[str]:
    return re.findall(r'<tr class="production-row[^"]*".*?</tr>', html, re.S)


def two_productions(client: AppClient) -> tuple[int, int]:
    first, second = add_article("A-1"), add_article("A-2")
    send(client, form([6], [(first, "3")], date="2026-11-10", note="Erste"))
    send(client, form([7, 8], [(first, "1"), (second, "4")], date="2026-11-12", note="Zweite"))
    return first, second


def test_the_list_shows_the_productions_newest_first(prepared_client: AppClient) -> None:
    two_productions(prepared_client)

    html = prepared_client.get("/production/list").text

    first_row, second_row = production_rows(html)
    assert "Zweite" in first_row and "12.11.2026" in first_row
    assert "2 " in first_row or "2</td>" in first_row  # two prints
    assert "A-1 x 1" in first_row and "A-2 x 4" in first_row
    assert "4,05" in first_row  # 2.75 + 1.30
    assert DE["production.state.active"] in first_row
    assert "Erste" in second_row and "A-1 x 3" in second_row and "4,5" in second_row


def test_the_list_can_be_sorted_by_date_and_cost(prepared_client: AppClient) -> None:
    two_productions(prepared_client)

    def notes(query: str) -> list[str]:
        html = prepared_client.get(f"/production/list{query}").text
        return [("Erste" if "Erste" in r else "Zweite") for r in production_rows(html)]

    assert notes("") == ["Zweite", "Erste"]
    assert notes("?sort=date&dir=asc") == ["Erste", "Zweite"]
    assert notes("?sort=cost&dir=desc") == ["Erste", "Zweite"]
    assert notes("?sort=cost&dir=asc") == ["Zweite", "Erste"]
    assert notes("?sort=nonsense&dir=up") == ["Zweite", "Erste"]


def test_an_empty_list_has_a_hint_and_the_tab_is_there(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/list").text

    assert DE["production.list.empty"] in html
    assert 'href="/production/list"' in prepared_client.get("/production").text


# --- Case 16: detail ---------------------------------------------------------------------------


def test_the_detail_shows_prints_and_articles(prepared_client: AppClient) -> None:
    first, second = add_article("A-1", "Halter"), add_article("A-2", "Clip")
    send(prepared_client, form([6, 8], [(first, "6"), (second, "4")], date="2026-11-10"))

    html = prepared_client.get("/production/1").text

    print_rows = re.findall(r'<tr class="detail-print[^"]*".*?</tr>', html, re.S)
    assert len(print_rows) == 2
    assert "print-failed" not in print_rows[0] and "print-failed" in print_rows[1]
    assert "4,5" in print_rows[0] and "1,3" in print_rows[1]
    article_rows = re.findall(r'<tr class="detail-article[^"]*".*?</tr>', html, re.S)
    assert "A-1" in article_rows[0] and ">6<" in article_rows[0].replace(" ", "").replace("\n", "")
    assert "3,48" in article_rows[0] and "0,58" in article_rows[0]  # 5.80 * 0.6 and per piece
    assert DE["production.split"] in article_rows[0]
    assert "10.11.2026" in html


def test_the_detail_of_a_single_article_has_no_split_flag(prepared_client: AppClient) -> None:
    article_id = add_article()
    send(prepared_client, form([6], [(article_id, "3")]))

    html = prepared_client.get("/production/1").text

    assert DE["production.split"] not in html
    assert "4,5" in html and "1,5" in html


def test_an_unknown_production_is_a_404(prepared_client: AppClient) -> None:
    for path in ("/production/99", "/production/99/cancel"):
        assert prepared_client.get(path).status_code == 404
    assert prepared_client.post("/production/99/cancel", data={}).status_code == 404
    huge = 10**30
    for path in (f"/production/{huge}", f"/production/{huge}/cancel"):
        assert prepared_client.get(path).status_code == 404
    assert prepared_client.post(f"/production/{huge}/cancel", data={}).status_code == 404


# --- Cases 17-18: cancelling -------------------------------------------------------------------


def test_cancelling_asks_first_and_then_frees_the_prints(prepared_client: AppClient) -> None:
    article_id = add_article()
    send(prepared_client, form([6, 7], [(article_id, "10")]))

    page = prepared_client.get("/production/1/cancel")
    assert page.status_code == 200
    assert DE["production.cancel.confirm"] in page.text
    assert 'action="/production/1/cancel"' in page.text
    assert stock(article_id) == 10 and not productions()[0].cancelled

    response = prepared_client.post("/production/1/cancel", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert stock(article_id) == 0
    assert productions()[0].cancelled
    listing = prepared_client.get("/production/list").text
    assert DE["production.state.cancelled"] in listing
    assert "Druck A" in names(prepared_client.get("/production").text)
    assert names(prepared_client.get("/production?view=assigned").text) == []
    assert DE["production.cancel"] not in production_rows(listing)[0]


def test_a_production_cannot_be_cancelled_twice(prepared_client: AppClient) -> None:
    article_id = add_article()
    send(prepared_client, form([6], [(article_id, "2")]))
    prepared_client.post("/production/1/cancel", data={})

    response = prepared_client.post("/production/1/cancel", data={})

    assert response.status_code == 422
    assert DE["production.error.cancelled_already"] in response.text
    assert count_movements() == 2


def test_cancelling_with_too_little_stock_says_so_and_changes_nothing(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    send(prepared_client, form([6], [(article_id, "10")]))
    add_movement(article_id, -4, reason="Verschenkt")

    response = prepared_client.post("/production/1/cancel", data={})

    assert response.status_code == 422
    assert DE["production.error.insufficient"] in response.text
    assert stock(article_id) == 6
    assert not productions()[0].cancelled
    assert count_movements() == 2


# --- Case 20: the stock list points to the production ------------------------------------------


def test_the_stock_list_links_to_the_production_instead_of_a_cancel_button(
    prepared_client: AppClient,
) -> None:
    article_id = add_article()
    send(prepared_client, form([6], [(article_id, "10")]))

    html = prepared_client.get("/stock/movements").text

    assert 'href="/production/1"' in html
    assert "/stock/movements/1/cancel" not in html


# --- Texts -------------------------------------------------------------------------------------


def test_all_texts_exist_in_both_languages() -> None:
    german = {key for key in DE if key.startswith(("production.", "log.action.production"))}
    english = {key for key in EN if key.startswith(("production.", "log.action.production"))}

    assert german
    assert german == english
