"""Filaments, part 4: stock per filament (issue #238). All data is invented.

The test case numbers refer to the confirmed list in the issue. The browser test is in
tests/browser/test_filament_stock.py. Bambuddy answers come from a mock.
"""

import json
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

from app import filament_stock
from app.bambuddy import Spool
from app.models import SpoolAssignment
from app.spool_list import SpoolCache
from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.test_filaments import LIST, db, make, stored
from tests.test_spool_assignment import linked, spool

D = Decimal
APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
EMPTY = DE["table.empty_value"]
NOW = datetime(2026, 11, 1, 12, 0, 0)


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_bambuddy_settings import TEST_SECRET_KEY

    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


def assign(
    filament_id: int,
    spool_id: int,
    price: str | None = "20",
    weight: str | None = "1000",
    minutes: int = 0,
) -> None:
    """A stored assignment (price and weight as values), `minutes` after the base time."""
    with db() as session:
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
        session.commit()


def cells(html: str, filament_name: str = "Ivory White") -> dict[str, str]:
    """The stock cells of the table row of one filament by column hook."""
    row = next(
        r for r in re.findall(r"<tr>.*?</tr>", html, re.DOTALL) if filament_name in r and "<td" in r
    )
    found = re.findall(r'<td class="[^"]*?cell-(stock|spools|avg|last)[^"]*">(.*?)</td>', row, re.S)
    return {key: " ".join(re.sub(r"<[^>]+>", " ", value).split()) for key, value in found}


def one_filament(client: AppClient, **overrides: str) -> int:
    """A filament without minimum, unless given."""
    make(client, **{"min_stock_kg": "", **overrides})
    return stored()[-1].id


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


# --- Case 1-4: stock --------------------------------------------------------------------------


def test_1_the_stock_is_the_sum_of_the_remaining_weights_of_the_active_spools(
    client: AppClient,
) -> None:
    linked(client, spool(1, weight_used=400), spool(2, weight_used=600))
    fid = one_filament(client)
    assign(fid, 1)
    assign(fid, 2)
    shown = cells(client.get(LIST).text)
    assert shown["stock"] == "1,00"
    assert shown["spools"] == "2"


def test_2_an_archived_spool_counts_neither_for_stock_nor_for_the_spool_count(
    client: AppClient,
) -> None:
    linked(
        client,
        spool(1, weight_used=400),
        spool(2, weight_used=600),
        spool(3, weight_used=0, archived_at="2026-11-01T10:00:00"),
    )
    fid = one_filament(client)
    for spool_id in (1, 2, 3):
        assign(fid, spool_id)
    shown = cells(client.get(LIST).text)
    assert shown["stock"] == "1,00" and shown["spools"] == "2"


def test_3_a_spool_that_is_gone_from_bambuddy_does_not_count(client: AppClient) -> None:
    linked(client, spool(1, weight_used=400))
    fid = one_filament(client)
    assign(fid, 1)
    assign(fid, 99)
    shown = cells(client.get(LIST).text)
    assert shown["stock"] == "0,60" and shown["spools"] == "1"


def test_4_a_filament_without_spools_has_zero_stock_and_no_prices(client: AppClient) -> None:
    linked(client, spool(1))
    one_filament(client)
    assert cells(client.get(LIST).text) == {
        "stock": "0,00",
        "spools": "0",
        "avg": EMPTY,
        "last": EMPTY,
    }


# --- Case 5-8: prices -------------------------------------------------------------------------


def test_5_the_average_price_is_weighted_by_grams(client: AppClient) -> None:
    linked(client, spool(1), spool(2))
    fid = one_filament(client)
    assign(fid, 1, price="20", weight="1000")
    assign(fid, 2, price="30", weight="3000")
    assert cells(client.get(LIST).text)["avg"] == "27,50"


def test_5_two_equal_spools_with_different_prices_give_the_plain_middle(
    client: AppClient,
) -> None:
    linked(client, spool(1), spool(2))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    assign(fid, 2, price="30")
    assert cells(client.get(LIST).text)["avg"] == "25,00"


def test_6_the_last_price_is_the_price_of_the_spool_assigned_last(client: AppClient) -> None:
    linked(client, spool(1), spool(2), spool(3))
    fid = one_filament(client)
    assign(fid, 1, price="20", minutes=0)
    assign(fid, 3, price="18", minutes=10)
    assign(fid, 2, price="30", minutes=5)
    assert cells(client.get(LIST).text)["last"] == "18,00"


def test_7_archived_and_deleted_spools_count_for_the_prices(client: AppClient) -> None:
    linked(client, spool(1, archived_at="2026-11-01T10:00:00"))
    fid = one_filament(client)
    assign(fid, 1, price="20", minutes=0)
    assign(fid, 99, price="30", minutes=5)
    shown = cells(client.get(LIST).text)
    assert shown["avg"] == "25,00" and shown["last"] == "30,00"
    assert shown["spools"] == "0"


def test_8_spools_without_price_are_left_out_and_never_counted_as_zero(
    client: AppClient,
) -> None:
    linked(client, spool(1), spool(2))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    assign(fid, 2, price=None, minutes=5)
    shown = cells(client.get(LIST).text)
    assert shown["avg"] == "20,00"
    # the newest spool has no price: the last price is the newest one that has one
    assert shown["last"] == "20,00"


def test_8_without_any_price_there_is_a_dash(client: AppClient) -> None:
    linked(client, spool(1))
    fid = one_filament(client)
    assign(fid, 1, price=None)
    shown = cells(client.get(LIST).text)
    assert shown["avg"] == EMPTY and shown["last"] == EMPTY


# --- Case 9-12: minimum and warning -------------------------------------------------------------


def warning(html: str, name: str = "Ivory White") -> bool:
    row = next(r for r in re.findall(r"<tr>.*?</tr>", html, re.DOTALL) if name in r and "<td" in r)
    return DE["stock.low"] in row


def test_9_below_the_minimum_the_warning_chip_shows(client: AppClient) -> None:
    linked(client, spool(1, weight_used=0))
    fid = one_filament(client, min_stock_kg="2")
    assign(fid, 1)
    html = client.get(LIST).text
    assert warning(html)
    assert 'class="chip chip-warn"' in html


def test_10_exactly_the_minimum_is_no_warning(client: AppClient) -> None:
    linked(client, spool(1, weight_used=0))
    fid = one_filament(client, min_stock_kg="1")
    assign(fid, 1)
    assert not warning(client.get(LIST).text)


def test_11_without_a_minimum_there_is_never_a_warning(client: AppClient) -> None:
    linked(client, spool(1))
    one_filament(client, min_stock_kg="")
    assert not warning(client.get(LIST).text)


def test_12_a_switched_off_filament_gets_no_warning(client: AppClient) -> None:
    linked(client, spool(1))
    fid = one_filament(client, min_stock_kg="5")
    assert warning(client.get(LIST).text)
    client.post(f"/filaments/{fid}/active", data={"active": "0"}, follow_redirects=False)
    assert not warning(client.get(LIST).text)


# --- Case 13-15: Bambuddy and speed ---------------------------------------------------------


def test_13_when_bambuddy_fails_the_page_stays_usable_without_stock_and_warning(
    client: AppClient,
) -> None:
    linked(client, spool(1))
    fid = one_filament(client, min_stock_kg="5")
    assign(fid, 1, price="20")
    client.app.state.bambuddy_transport = httpx2.MockTransport(lambda r: httpx2.Response(500))
    response = client.get(LIST)
    assert response.status_code == 200
    shown = cells(response.text)
    assert shown["stock"] == EMPTY and shown["spools"] == EMPTY
    assert shown["avg"] == "20,00" and shown["last"] == "20,00"
    assert DE["filaments.stock.unreadable"] in response.text
    assert not warning(response.text)


def test_13_without_assignments_bambuddy_is_not_asked_and_there_is_no_hint(
    client: AppClient,
) -> None:
    bambuddy = linked(client, spool(1))
    one_filament(client)
    bambuddy.requests.clear()
    html = client.get(LIST).text
    assert DE["filaments.stock.unreadable"] not in html
    assert cells(html)["stock"] == "0,00"


def test_14_two_page_views_in_a_row_ask_bambuddy_only_once(client: AppClient) -> None:
    bambuddy = linked(client, spool(1))
    fid = one_filament(client)
    assign(fid, 1)
    bambuddy.requests.clear()
    client.get(LIST)
    client.get(LIST)
    spool_requests = [r for r in bambuddy.requests if r.url.path == "/api/v1/inventory/spools"]
    assert len(spool_requests) == 1


def test_14_the_cache_expires_after_its_time() -> None:
    clock = [1000.0]
    cache = SpoolCache(ttl=90, clock=lambda: clock[0])
    calls: list[int] = []

    def fetch():
        calls.append(1)
        return filament_stock_list()

    cache.get("key", fetch)
    clock[0] += 89
    cache.get("key", fetch)
    assert len(calls) == 1
    clock[0] += 2
    cache.get("key", fetch)
    assert len(calls) == 2


def test_14_a_problem_is_not_kept_in_the_cache() -> None:
    from app.spool_list import SpoolList

    cache = SpoolCache(ttl=90, clock=lambda: 0.0)
    calls: list[int] = []

    def fetch():
        calls.append(1)
        return SpoolList(problem="bambuddy.error.unreachable")

    cache.get("key", fetch)
    cache.get("key", fetch)
    assert len(calls) == 2


def filament_stock_list():
    from app.spool_list import SpoolList

    return SpoolList([])


def test_15_five_thousand_spools_stay_fast_with_one_bambuddy_request(client: AppClient) -> None:
    spools = [spool(n, weight_used=100) for n in range(1, 5001)]
    bambuddy = linked(client, *spools)
    ids = []
    for n in range(200):
        make(client, color_name=f"Farbe {n}")
        ids.append(stored()[-1].id)
    with db() as session:
        for n in range(1, 5001):
            session.add(
                SpoolAssignment(
                    spool_id=n,
                    filament_id=ids[n % 200],
                    price_per_kg=D("20"),
                    weight_g=D("1000"),
                    spool_key="k",
                    assigned_at=NOW + timedelta(minutes=n),
                )
            )
        session.commit()
    bambuddy.requests.clear()
    started = time.perf_counter()
    response = client.get(LIST)
    took = time.perf_counter() - started
    assert response.status_code == 200
    assert took < 2.0
    assert len([r for r in bambuddy.requests if r.url.path.endswith("/spools")]) == 1
    assert cells(response.text, "Farbe 0")["spools"] == "25"


# --- Case 16: rights ---------------------------------------------------------------------------


def test_16_everyone_signed_in_sees_the_stock_but_cannot_change(
    client: AppClient, reader: AppClient
) -> None:
    linked(client, spool(1, weight_used=0))
    fid = one_filament(client)
    assign(fid, 1)
    reader.app.state.bambuddy_transport = client.app.state.bambuddy_transport
    html = reader.get(LIST).text
    assert cells(html)["stock"] == "1,00"
    assert "/edit" not in html


# --- The calculation itself ---------------------------------------------------------------------


def bambuddy_spool(spool_id: int, label: str, used: str, archived: bool = False) -> Spool:
    return Spool(
        id=spool_id,
        material="PLA",
        color_name=None,
        brand=None,
        cost_per_kg=None,
        label_weight=D(label) if label else None,
        weight_used=D(used),
        category=None,
        archived=archived,
    )


def stored_assignment(spool_id: int, price: str | None, weight: str | None, minutes: int = 0):
    return filament_stock.Held(
        spool_id,
        D(price) if price is not None else None,
        D(weight) if weight is not None else None,
        NOW + timedelta(minutes=minutes),
    )


def test_a_spool_with_unknown_weight_counts_as_a_spool_but_adds_no_grams() -> None:
    held = [stored_assignment(1, "20", "1000")]
    live = {1: bambuddy_spool(1, "", "0")}
    result = filament_stock.summarize(held, live, None)
    assert result.spool_count == 1 and result.stock_kg == D("0")


def test_the_remaining_weight_is_never_negative() -> None:
    held = [stored_assignment(1, "20", "1000")]
    live = {1: bambuddy_spool(1, "1000", "1200")}
    assert filament_stock.summarize(held, live, None).stock_kg == D("0")


def test_without_live_data_stock_and_warning_are_unknown_but_prices_stay() -> None:
    held = [stored_assignment(1, "20", "1000")]
    result = filament_stock.summarize(held, None, D("5"))
    assert result.stock_kg is None and result.spool_count is None and not result.low
    assert result.average_price == D("20") and result.last_price == D("20")


def test_a_price_without_weight_is_not_part_of_the_average_but_may_be_the_last_price() -> None:
    held = [stored_assignment(1, "20", "1000"), stored_assignment(2, "40", None, minutes=5)]
    result = filament_stock.summarize(held, {}, None)
    assert result.average_price == D("20") and result.last_price == D("40")


# --- Texts -------------------------------------------------------------------------------------


def test_all_stock_texts_exist_in_german_and_english() -> None:
    keys = [
        "filament.col.stock",
        "filament.col.spools",
        "filament.col.avg_price",
        "filament.col.last_price",
        "filaments.stock.unreadable",
    ]
    for key in keys:
        assert DE.get(key) and EN.get(key), key
