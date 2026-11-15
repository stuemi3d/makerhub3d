"""Bambuddy client (issue #117, test cases 1-10). Uses recorded, cleaned answers only."""

import json
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

from app.bambuddy import BambuddyClient, BambuddyError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bambuddy"
BASE = "http://bambuddy.example.com:8000"
KEY = "test-key-nur-fuer-tests"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


ROUTES = {
    "/api/v1/archives/": "archives.json",
    "/api/v1/archives/260": "archive_detail.json",
    "/api/v1/inventory/spools": "spools.json",
    "/api/v1/printers/": "printers.json",
    "/api/v1/settings/": "settings.json",
}


def make_client(handler=None, requests: list | None = None) -> BambuddyClient:
    def default(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        return httpx2.Response(200, json=fixture(ROUTES[request.url.path]))

    return BambuddyClient(BASE, KEY, transport=httpx2.MockTransport(handler or default))


# --- Test cases 1-5: reading ---------------------------------------------------------------


def test_archive_list_has_the_print_data() -> None:
    archives = make_client().archives()
    assert [a.id for a in archives] == [261, 260, 259, 258]
    done = archives[1]
    assert done.name == "Beispiel Clip - Plate 1"
    assert done.status == "completed"
    assert done.printer_id == 1
    assert done.print_seconds == 766  # the actual time
    assert done.filament_grams == Decimal("1.56")
    assert done.energy_kwh == Decimal("0.0337")
    assert done.completed_at is not None
    assert archives[3].status == "failed"
    assert archives[0].energy_kwh is None  # still printing


def test_money_and_weights_are_decimals_not_floats() -> None:
    done = make_client().archives()[1]
    assert isinstance(done.filament_grams, Decimal)
    assert isinstance(done.energy_cost, Decimal)
    assert done.energy_cost == Decimal("0.008")


def test_archive_detail_has_material_time_and_energy() -> None:
    archive = make_client().archive(260)
    assert archive.id == 260
    assert archive.filament_grams == Decimal("1.56")
    assert archive.print_seconds == 766
    assert archive.energy_kwh == Decimal("0.0337")
    assert archive.material_cost == Decimal("0.01")


def test_spools_have_material_colour_price_and_category() -> None:
    spools = make_client().spools()
    assert len(spools) == 3
    first = spools[0]
    assert (first.material, first.color_name) == ("ASA", "Grey")
    assert first.cost_per_kg == Decimal("27.5")
    assert first.category is None
    assert spools[2].category == "geschäftlich"


def test_printers_have_name_and_model() -> None:
    printers = make_client().printers()
    assert [(p.id, p.name, p.model) for p in printers] == [(1, "Beispiel-Drucker", "P2S")]


def test_electricity_price_is_read() -> None:
    price = make_client().electricity_price()
    assert price.per_kwh == Decimal("0.23")
    assert price.currency == "EUR"


def test_no_secrets_from_the_settings_end_up_in_the_result() -> None:
    # Bambuddy's settings hold tokens and passwords; the client keeps only the price.
    body = {"energy_cost_per_kwh": 0.3, "currency": "EUR", "mqtt_password": "geheim-12345"}
    client = make_client(lambda request: httpx2.Response(200, json=body))
    assert "geheim" not in repr(client.electricity_price())


# --- Test case 6: only reading --------------------------------------------------------------


def test_only_get_requests_with_the_key_are_sent() -> None:
    requests: list[httpx2.Request] = []
    client = make_client(requests=requests)
    client.archives()
    client.archive(260)
    client.spools()
    client.printers()
    client.electricity_price()
    assert len(requests) == 5
    assert {r.method for r in requests} == {"GET"}
    assert {r.headers["X-API-Key"] for r in requests} == {KEY}
    for name in ("post", "put", "patch", "delete"):
        assert not hasattr(client, name)


def test_redirects_are_not_followed_so_the_key_stays_with_bambuddy() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(302, headers={"Location": "http://other.example.org/"})

    with pytest.raises(BambuddyError) as error:
        make_client(handler).printers()
    assert error.value.code == "bad_response"
    assert len(requests) == 1


# --- Test cases 7-10: errors ----------------------------------------------------------------


def error_code(handler) -> str:
    with pytest.raises(BambuddyError) as error:
        make_client(handler).printers()
    return error.value.code


def test_unreachable_bambuddy_gives_a_clear_error_without_url_or_key() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("boom http://bambuddy.example.com:8000 " + KEY)

    with pytest.raises(BambuddyError) as error:
        make_client(handler).printers()
    assert error.value.code == "unreachable"
    assert KEY not in str(error.value) and "example.com" not in str(error.value)


def test_slow_bambuddy_gives_a_timeout_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow")

    assert error_code(handler) == "timeout"


def test_wrong_key_is_reported_as_rejected() -> None:
    assert error_code(lambda request: httpx2.Response(401, json={"detail": "x"})) == "rejected"


def test_missing_right_says_so_and_names_the_path() -> None:
    with pytest.raises(BambuddyError) as error:
        make_client(lambda request: httpx2.Response(403, json={"detail": "no"})).spools()
    assert error.value.code == "forbidden"
    assert error.value.path == "/api/v1/inventory/spools"


def test_server_error_is_reported() -> None:
    assert error_code(lambda request: httpx2.Response(500, text="Traceback ...")) == "server_error"


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text="<html>not json</html>"),
        httpx2.Response(200, json={"unexpected": "object"}),
        httpx2.Response(200, json=[{"id": "x"}]),
    ],
)
def test_unexpected_data_gives_an_error_instead_of_a_crash(response: httpx2.Response) -> None:
    assert error_code(lambda request: response) == "bad_response"


def test_a_broken_archive_entry_is_reported_as_bad_response() -> None:
    entries = fixture("archives.json")
    entries[1]["status"] = None
    client = make_client(lambda request: httpx2.Response(200, json=entries))
    with pytest.raises(BambuddyError) as error:
        client.archives()
    assert error.value.code == "bad_response"


# --- Test case 14: only valid addresses -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://bambuddy.example.com",
        "bambuddy.example.com",
        "http://",
        "http://user:pw@bambuddy.example.com",
        "http://bambuddy.example.com/path?x=1",
        "http://bambuddy example.com",
        "javascript:alert(1)",
    ],
)
def test_invalid_addresses_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        BambuddyClient(url, KEY)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8000", "https://bambuddy.example.com", "http://localhost:8000/"],
)
def test_valid_addresses_are_accepted(url: str) -> None:
    BambuddyClient(url, KEY)
