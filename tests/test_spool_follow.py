"""Filaments, part 5: follow the assignment of the spools (issue #245). All data is invented.

The test case numbers refer to the confirmed list in the issue. Case 16 (browser) is in
tests/browser/test_spool_follow.py. Bambuddy answers come from a mock.
"""

import json
import re
from pathlib import Path

import httpx2
import pytest

from app.models import ChangeLogEntry
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_filament_stock import assign, one_filament
from tests.test_filaments import db, log_actions, stored
from tests.test_spool_assignment import PAGE, assignments, linked, spool

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
ARCHIVED = "2026-11-01T10:00:00"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_bambuddy_settings import TEST_SECRET_KEY

    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


@pytest.fixture
def employee(client: AppClient) -> AppClient:
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as test_client:
        yield test_client


def spools_page(fid: int) -> str:
    return f"/filaments/{fid}/spools"


def row(html: str, spool_id: int) -> str:
    found = re.search(rf'<tr id="assigned-{spool_id}".*?</tr>', html, re.DOTALL)
    assert found, f"no row for spool {spool_id}"
    return found.group(0)


def text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def state_of(html: str, spool_id: int) -> str:
    cell = re.search(r'<td class="cell-state">(.*?)</td>', row(html, spool_id), re.DOTALL)
    assert cell
    return text(cell.group(1))


def stored_price(spool_id: int):
    return next(a for a in assignments() if a.spool_id == spool_id).price_per_kg


def assignments_price(spool_id: int) -> float:
    return float(stored_price(spool_id))


def last_log() -> ChangeLogEntry:
    with db() as session:
        return session.query(ChangeLogEntry).order_by(ChangeLogEntry.id.desc()).first()


# --- Case 1-4: state ---------------------------------------------------------------------------


def test_1_an_active_spool_is_shown_as_active_and_counts_for_the_stock(client: AppClient) -> None:
    linked(client, spool(1, weight_used=400))
    fid = one_filament(client)
    assign(fid, 1)
    html = client.get(spools_page(fid)).text
    assert state_of(html, 1) == DE["filaments.state.active"]


def test_2_an_archived_spool_is_shown_as_archived(client: AppClient) -> None:
    linked(client, spool(1, archived_at=ARCHIVED))
    fid = one_filament(client)
    assign(fid, 1)
    assert state_of(client.get(spools_page(fid)).text, 1) == DE["filaments.state.archived"]


def test_3_a_deleted_spool_is_shown_as_gone_and_keeps_its_stored_values(
    client: AppClient,
) -> None:
    linked(client, spool(2))
    fid = one_filament(client)
    assign(fid, 1, price="20", weight="1000")
    html = client.get(spools_page(fid)).text
    assert state_of(html, 1) == DE["filaments.state.gone"]
    assert "20,00" in row(html, 1)
    assert stored_price(1) is not None


def test_4_when_bambuddy_cannot_be_read_nothing_is_marked_as_gone(client: AppClient) -> None:
    linked(client, spool(1))
    fid = one_filament(client)
    assign(fid, 1)
    client.app.state.bambuddy_transport = httpx2.MockTransport(lambda r: httpx2.Response(500))
    response = client.get(spools_page(fid))
    assert response.status_code == 200
    assert DE["filaments.state.gone"] not in response.text
    assert DE["filaments.state.unknown"] in row(response.text, 1)
    assert DE["filaments.spools.unreadable"] in response.text
    assert len(assignments()) == 1


# --- Case 5-10: price changed ------------------------------------------------------------------


def hint(html: str, spool_id: int) -> str | None:
    found = re.search(r'<small class="price-hint">(.*?)</small>', row(html, spool_id), re.DOTALL)
    return text(found.group(1)) if found else None


def test_5_the_same_price_gives_no_hint(client: AppClient) -> None:
    linked(client, spool(1, cost_per_kg=20))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    assert hint(client.get(spools_page(fid)).text, 1) is None


def test_6_a_different_price_shows_old_and_new_and_changes_nothing_by_itself(
    client: AppClient,
) -> None:
    linked(client, spool(1, cost_per_kg=25))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    html = client.get(spools_page(fid)).text
    shown = hint(html, 1)
    assert shown and "20,00" in shown and "25,00" in shown
    assert str(stored_price(1)) == "20.00" or str(stored_price(1)) == "20"
    assert "/filaments/spools/1/price" in html


def test_7_taking_over_stores_the_new_price_and_writes_the_log(client: AppClient) -> None:
    linked(client, spool(1, cost_per_kg=25))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    page = client.get("/filaments/spools/1/price")
    assert page.status_code == 200 and "20,00" in page.text and "25,00" in page.text
    response = client.post("/filaments/spools/1/price", follow_redirects=False)
    assert response.status_code == 303
    assert float(stored_price(1)) == 25.0
    assert hint(client.get(spools_page(fid)).text, 1) is None
    assert "filament.spool_price_taken" in log_actions()
    details = json.loads(last_log().details)
    assert details[0]["old"] == "20.00" and details[0]["new"] == "25.00"


def test_8_a_spool_that_had_no_price_gets_the_same_hint(client: AppClient) -> None:
    linked(client, spool(1, cost_per_kg=25))
    fid = one_filament(client)
    assign(fid, 1, price=None)
    shown = hint(client.get(spools_page(fid)).text, 1)
    assert shown and "25,00" in shown
    client.post("/filaments/spools/1/price", follow_redirects=False)
    assert float(stored_price(1)) == 25.0


def test_9_a_deleted_spool_gets_no_price_hint_and_cannot_take_over(client: AppClient) -> None:
    linked(client, spool(2))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    assert hint(client.get(spools_page(fid)).text, 1) is None
    assert client.post("/filaments/spools/1/price", follow_redirects=False).status_code == 404
    assert float(stored_price(1)) == 20.0


def test_10_a_reader_sees_the_hint_but_cannot_take_over(
    client: AppClient, reader: AppClient
) -> None:
    linked(client, spool(1, cost_per_kg=25))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    reader.app.state.bambuddy_transport = client.app.state.bambuddy_transport
    html = reader.get(spools_page(fid)).text
    assert hint(html, 1)
    assert "/filaments/spools/1/price" not in html
    assert reader.post("/filaments/spools/1/price", follow_redirects=False).status_code == 403
    assert reader.get("/filaments/spools/1/price").status_code == 403
    assert float(stored_price(1)) == 20.0


# --- Case 11-14: change or release -------------------------------------------------------------


def test_11_moving_a_spool_keeps_its_values_and_writes_the_log(client: AppClient) -> None:
    linked(client, spool(1, cost_per_kg=20))
    first = one_filament(client)
    second = one_filament(client, color_name="Jade White")
    assign(first, 1, price="20", weight="1000")
    page = client.get("/filaments/spools/1/move")
    assert page.status_code == 200 and "Jade White" in page.text
    response = client.post(
        "/filaments/spools/1/move", data={"filament": str(second)}, follow_redirects=False
    )
    assert response.status_code == 303
    moved = assignments()[0]
    assert moved.filament_id == second
    assert float(moved.price_per_kg) == 20.0 and float(moved.weight_g) == 1000.0
    assert state_of(client.get(spools_page(second)).text, 1)
    assert "filament.spool_moved" in log_actions()
    assert client.get(spools_page(first)).text.count('id="assigned-1"') == 0


def test_11_an_invented_target_filament_changes_nothing(client: AppClient) -> None:
    linked(client, spool(1))
    first = one_filament(client)
    assign(first, 1)
    for value in ("999", "abc", "", "²", "٣", "9" * 30):
        client.post("/filaments/spools/1/move", data={"filament": value}, follow_redirects=False)
    assert assignments()[0].filament_id == first


def test_12_releasing_makes_the_spool_wait_again(client: AppClient) -> None:
    linked(client, spool(1))
    fid = one_filament(client)
    assign(fid, 1)
    page = client.get("/filaments/spools/1/release")
    assert page.status_code == 200 and DE["filaments.release.button"] in page.text
    response = client.post("/filaments/spools/1/release", follow_redirects=False)
    assert response.status_code == 303
    assert assignments() == []
    assert 'id="spool-1"' in client.get(PAGE).text
    assert "filament.spool_released" in log_actions()


def test_13_a_reader_can_neither_move_nor_release_an_employee_can(
    client: AppClient, reader: AppClient, employee: AppClient
) -> None:
    linked(client, spool(1))
    first = one_filament(client)
    second = one_filament(client, color_name="Jade White")
    assign(first, 1)
    for other in (reader, employee):
        other.app.state.bambuddy_transport = client.app.state.bambuddy_transport
    html = reader.get(spools_page(first)).text
    assert "/filaments/spools/1/move" not in html and "/filaments/spools/1/release" not in html
    for address, data in (
        ("/filaments/spools/1/move", {"filament": str(second)}),
        ("/filaments/spools/1/release", {}),
    ):
        assert reader.post(address, data=data, follow_redirects=False).status_code == 403
    assert assignments()[0].filament_id == first
    employee.post(
        "/filaments/spools/1/move", data={"filament": str(second)}, follow_redirects=False
    )
    assert assignments()[0].filament_id == second


def test_14_a_filament_with_spools_cannot_be_deleted_until_the_last_is_released(
    client: AppClient,
) -> None:
    linked(client, spool(1))
    fid = one_filament(client)
    assign(fid, 1)
    assert client.post(f"/filaments/{fid}/delete", follow_redirects=False).status_code == 409
    client.post("/filaments/spools/1/release", follow_redirects=False)
    assert client.post(f"/filaments/{fid}/delete", follow_redirects=False).status_code in (200, 303)
    assert stored() == []


def test_14_unknown_spool_or_filament_gives_404(client: AppClient) -> None:
    linked(client, spool(1))
    assert client.get("/filaments/999/spools").status_code == 404
    assert client.get("/filaments/spools/5/release").status_code == 404
    assert client.get("/filaments/spools/5/move").status_code == 404


# --- Case 15: always fresh ---------------------------------------------------------------------


def test_15_a_price_changed_and_changed_back_shows_no_hint_at_once(client: AppClient) -> None:
    bambuddy = linked(client, spool(1, cost_per_kg=0))
    fid = one_filament(client)
    assign(fid, 1, price="0")
    bambuddy.spools[0]["cost_per_kg"] = 4
    assert hint(client.get(spools_page(fid)).text, 1)
    bambuddy.spools[0]["cost_per_kg"] = 0
    assert hint(client.get(spools_page(fid)).text, 1) is None
    assert "data-price-chip" not in client.get("/filaments").text
    assert "/filaments/spools/1/price" not in client.get("/filaments/price-changes").text


def test_15_every_view_asks_bambuddy_once_and_a_state_change_shows_at_once(
    client: AppClient,
) -> None:
    bambuddy = linked(client, spool(1, cost_per_kg=25), spool(2))
    fid = one_filament(client)
    assign(fid, 1, price="20")

    def spool_requests() -> int:
        return sum(1 for r in bambuddy.requests if r.url.path.endswith("/inventory/spools"))

    bambuddy.requests.clear()
    client.get(spools_page(fid))
    assert spool_requests() == 1
    bambuddy.spools[0]["archived_at"] = ARCHIVED
    assert state_of(client.get(spools_page(fid)).text, 1) == DE["filaments.state.archived"]


def test_texts_exist_in_both_languages() -> None:
    keys = [
        "filaments.state.active",
        "filaments.state.archived",
        "filaments.state.gone",
        "filaments.state.unknown",
        "filaments.spools.unreadable",
        "filaments.release.button",
        "log.action.filament.spool_price_taken",
        "log.action.filament.spool_moved",
        "log.action.filament.spool_released",
    ]
    for key in keys:
        assert DE.get(key) and EN.get(key), key
