"""Production: notice when Bambuddy charged the default filament price (issue #228).
All data is invented; Bambuddy answers come from a mock. The test case numbers refer to the
comment on issue #228.
"""

from datetime import datetime
from decimal import Decimal

import httpx2
import pytest

from app.bambuddy import BambuddyClient, BambuddyError, Spool, Usage
from app.default_price import standard_price_spools
from tests.auth import AppClient
from tests.machine import add_printer
from tests.test_bambuddy_settings import TEST_SECRET_KEY, answer_with
from tests.test_production_booking import form, productions, send
from tests.test_production_page import prepared, raw
from tests.test_stock import add_article

D = Decimal
END = datetime(2026, 11, 15, 12, 0, 0)
DEFAULT = D("25")


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


def spool(spool_id: int, price: str | None) -> Spool:
    return Spool(
        id=spool_id,
        material="PLA",
        color_name="Weiß",
        brand=None,
        cost_per_kg=D(price) if price is not None else None,
        label_weight=D(1000),
        weight_used=D(0),
        category=None,
    )


def usage(spool_id: int, grams: str, cost: str, printer: int = 1, at: datetime = END) -> Usage:
    return Usage(spool_id, D(grams), D(cost), printer, at)


def found(usages: list[Usage], spools: list[Spool]) -> list[int]:
    by_id = {s.id: s for s in spools}
    return [s.id for s in standard_price_spools(END, 1, usages, by_id, DEFAULT)]


# --- Cases 1-6: the detection itself ------------------------------------------------------------


def test_1_a_spool_without_price_charged_at_the_default_price() -> None:
    assert found([usage(26, "181.5", "4.54")], [spool(26, None)]) == [26]


def test_2_a_spool_with_a_price_and_matching_cost_is_fine() -> None:
    assert found([usage(5, "100", "2.60")], [spool(5, "25.99")]) == []


def test_3_a_spool_with_price_0_and_cost_0_is_fine() -> None:
    assert found([usage(26, "181.5", "0")], [spool(26, "0")]) == []


def test_4_a_spool_with_price_0_but_default_price_charged() -> None:
    assert found([usage(26, "181.5", "4.54")], [spool(26, "0")]) == [26]


def test_5_a_real_price_equal_to_the_default_price_is_fine() -> None:
    assert found([usage(5, "181.5", "4.54")], [spool(5, "25")]) == []


def test_6_several_spools_name_every_affected_one() -> None:
    usages = [usage(26, "100", "2.50"), usage(5, "50", "1.30"), usage(10, "20", "0.50")]
    spools = [spool(26, None), spool(5, "25.99"), spool(10, None)]
    assert found(usages, spools) == [26, 10]


def test_usage_of_other_prints_or_printers_does_not_count() -> None:
    other_time = datetime(2026, 11, 15, 14, 0, 0)
    usages = [usage(26, "100", "2.50", printer=2), usage(26, "100", "2.50", at=other_time)]
    assert found(usages, [spool(26, None)]) == []


def test_unknown_spool_and_missing_cost_are_skipped() -> None:
    usages = [usage(99, "100", "2.50"), Usage(26, D(100), None, 1, END)]
    assert found(usages, [spool(26, None)]) == []


def test_seconds_apart_still_belong_to_the_same_print() -> None:
    late = datetime(2026, 11, 15, 12, 0, 20)
    assert found([usage(26, "100", "2.50", at=late)], [spool(26, None)]) == [26]


# --- The client reads the usage entries --------------------------------------------------------


def client_with(handler) -> BambuddyClient:
    return BambuddyClient("http://bambuddy.example", "key", transport=httpx2.MockTransport(handler))


def test_the_client_reads_usage_and_the_default_price() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/v1/inventory/usage":
            assert request.url.params["limit"]
            return httpx2.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "spool_id": 26,
                        "weight_used": 181.5,
                        "cost": 4.54,
                        "printer_id": 1,
                        "created_at": "2026-11-15T12:00:00",
                        "status": "completed",
                    }
                ],
            )
        return httpx2.Response(200, json={"default_filament_cost": 25.0, "currency": "EUR"})

    client = client_with(handler)
    assert client.usage() == [Usage(26, D("181.5"), D("4.54"), 1, END)]
    assert client.default_filament_cost() == D("25.0")


def test_the_client_reports_a_broken_usage_answer() -> None:
    client = client_with(lambda request: httpx2.Response(200, json={"oops": 1}))
    with pytest.raises(BambuddyError):
        client.usage()


# --- Cases 7-10: the production page ----------------------------------------------------------

ARCHIVES = [
    raw(
        6,
        "2026-11-15T08:00:00",
        name="Druck A",
        cost=4.54,
        completed_at="2026-11-15T12:00:00.463857",
    )
]
SPOOLS = [
    {"id": 26, "material": "PLA", "color_name": "Weiß", "cost_per_kg": None},
    {"id": 5, "material": "PLA", "color_name": "Rot", "cost_per_kg": 25.99},
]
USAGE = {
    "id": 1,
    "spool_id": 26,
    "weight_used": 181.5,
    "cost": 4.54,
    "printer_id": 1,
    "created_at": "2026-11-15T12:00:00",
    "status": "completed",
}
HINT = "Standardpreis"


def bambuddy(
    client: AppClient, usages: list[dict], usage_status: int = 200, spools: list[dict] = SPOOLS
) -> None:
    """The archive as in `prepared`, plus spools, settings and the usage list."""
    prepared(client, ARCHIVES)

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/api/v1/archives/":
            offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
            return httpx2.Response(200, json=ARCHIVES[offset : offset + limit])
        if path == "/api/v1/inventory/usage":
            return httpx2.Response(usage_status, json=usages)
        if path == "/api/v1/inventory/spools":
            return httpx2.Response(200, json=spools)
        if path == "/api/v1/settings/":
            return httpx2.Response(200, json={"default_filament_cost": 25.0, "currency": "EUR"})
        return httpx2.Response(404)

    answer_with(client, handler)
    add_printer("12.00")


def preview(client: AppClient, article_id: int) -> str:
    return send(client, form([6], (article_id, "1"), action="preview")).text


def test_7_no_usage_entry_for_the_print_means_no_notice(client: AppClient) -> None:
    bambuddy(client, [])
    page = client.get("/production/new?prints=6")
    assert page.status_code == 200 and HINT not in page.text


def test_8_a_failing_usage_request_does_not_stop_the_mask_or_the_booking(
    client: AppClient,
) -> None:
    bambuddy(client, [], usage_status=500)
    page = client.get("/production/new?prints=6")
    assert page.status_code == 200 and HINT not in page.text
    assert 'name="cost_material_6"' in page.text
    assert send(client, form([6], (add_article(), "1"))).status_code == 303


def test_9_mask_and_preview_show_the_notice_and_booking_still_works(client: AppClient) -> None:
    bambuddy(client, [USAGE])
    page = client.get("/production/new?prints=6")
    assert page.status_code == 200
    assert HINT in page.text and "Weiß" in page.text
    article_id = add_article()
    assert HINT in preview(client, article_id)
    assert send(client, form([6], (article_id, "1"))).status_code == 303


def test_9_a_spool_with_a_real_price_gives_no_notice(client: AppClient) -> None:
    bambuddy(client, [{**USAGE, "spool_id": 5, "cost": 4.72}])
    assert HINT not in client.get("/production/new?prints=6").text


def test_10_booking_stores_what_was_sent_and_nothing_about_the_notice(
    client: AppClient,
) -> None:
    bambuddy(client, [USAGE])
    article_id = add_article()
    send(client, form([6], (article_id, "1"), cost_material_6="0"))
    (item,) = productions()
    assert item.prints[0].material_cost == D(0)
    assert HINT not in client.get(f"/production/{item.id}").text


def test_a_spool_name_with_markup_is_escaped_in_the_notice(client: AppClient) -> None:
    evil = [{**SPOOLS[0], "color_name": "<script>x</script>"}, SPOOLS[1]]
    bambuddy(client, [USAGE], spools=evil)
    page = client.get("/production/new?prints=6").text
    assert HINT in page and "<script>x</script>" not in page
    assert "&lt;script&gt;" in page
