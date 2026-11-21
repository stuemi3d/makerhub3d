"""Filaments, part 5b: a changed spool price is easy to see (issue #245, cases 17-24).

The browser test (case 24) is in tests/browser/test_spool_notice.py. All data is invented.
"""

import json
import re
from pathlib import Path

import httpx2
import pytest

from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.test_filament_stock import assign, one_filament
from tests.test_filaments import LIST
from tests.test_spool_assignment import linked, spool
from tests.test_spool_follow import assignments_price

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
OVERVIEW = "/filaments/price-changes"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_bambuddy_settings import TEST_SECRET_KEY

    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def reader(client: AppClient) -> AppClient:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as test_client:
        yield test_client


def chip(html: str) -> str | None:
    found = re.search(r"<a[^>]*data-price-chip[^>]*>(.*?)</a>", html, re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", found.group(1)).split()) if found else None


def notice(html: str) -> str | None:
    found = re.search(r"<p[^>]*data-price-notice[^>]*>(.*?)</p>", html, re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", found.group(1)).split()) if found else None


def two_filaments(client: AppClient) -> tuple[int, int]:
    first = one_filament(client)
    second = one_filament(client, color_name="Jade White")
    return first, second


# --- Case 17-19: chip ----------------------------------------------------------------------


def test_17_a_changed_price_shows_a_chip_with_the_count_that_leads_to_the_spools(
    client: AppClient,
) -> None:
    linked(client, spool(1, cost_per_kg=25), spool(2, cost_per_kg=30), spool(3, cost_per_kg=20))
    fid = one_filament(client)
    for spool_id in (1, 2, 3):
        assign(fid, spool_id, price="20")
    html = client.get(LIST).text
    assert chip(html) == DE["filaments.price.chip"].replace("{n}", "2")
    assert f'href="/filaments/{fid}/spools"' in re.search(
        r"<a[^>]*data-price-chip[^>]*>", html
    ).group(0)


@pytest.mark.parametrize(
    "live",
    [
        {"cost_per_kg": 20},  # same price
        {"cost_per_kg": None},  # no price in Bambuddy
    ],
)
def test_18_no_chip_without_a_difference(client: AppClient, live: dict) -> None:
    linked(client, spool(1, **live))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    html = client.get(LIST).text
    assert chip(html) is None and notice(html) is None


def test_18_no_chip_for_a_deleted_spool_or_when_bambuddy_cannot_be_read(
    client: AppClient,
) -> None:
    linked(client, spool(2, cost_per_kg=25))
    fid = one_filament(client)
    assign(fid, 1, price="20")  # not in Bambuddy any more
    assert chip(client.get(LIST).text) is None
    assign(fid, 2, price="20")
    client.app.state.bambuddy_transport = httpx2.MockTransport(lambda r: httpx2.Response(500))
    html = client.get(LIST).text
    assert chip(html) is None and notice(html) is None


def test_19_a_reader_sees_the_chip_too(client: AppClient, reader: AppClient) -> None:
    linked(client, spool(1, cost_per_kg=25))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    reader.app.state.bambuddy_transport = client.app.state.bambuddy_transport
    assert chip(reader.get(LIST).text) == DE["filaments.price.chip"].replace("{n}", "1")


# --- Case 20-23: notice and overview ----------------------------------------------------------


def test_20_the_notice_above_the_table_counts_all_spools_and_links_to_the_overview(
    client: AppClient,
) -> None:
    linked(client, spool(1, cost_per_kg=25), spool(2, cost_per_kg=30), spool(3, cost_per_kg=20))
    first, second = two_filaments(client)
    assign(first, 1, price="20")
    assign(second, 2, price="20")
    assign(second, 3, price="20")
    html = client.get(LIST).text
    assert (notice(html) or "").startswith(DE["filaments.price.notice"].replace("{n}", "2"))
    assert f'href="{OVERVIEW}"' in html


def test_20_without_changes_there_is_no_notice(client: AppClient) -> None:
    linked(client, spool(1, cost_per_kg=20))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    assert notice(client.get(LIST).text) is None


def test_21_the_overview_lists_all_changed_spools_and_taking_over_removes_the_row(
    client: AppClient,
) -> None:
    linked(client, spool(1, cost_per_kg=25), spool(2, cost_per_kg=30), spool(3, cost_per_kg=20))
    first, second = two_filaments(client)
    assign(first, 1, price="20")
    assign(second, 2, price="20")
    assign(second, 3, price="20")
    html = client.get(OVERVIEW).text
    assert 'id="change-1"' in html and 'id="change-2"' in html and 'id="change-3"' not in html
    assert "Jade White" in html and "20,00" in html and "25,00" in html
    assert "/filaments/spools/1/price" in html
    client.post("/filaments/spools/1/price", follow_redirects=False)
    assert assignments_price(1) == 25
    after = client.get(OVERVIEW).text
    assert 'id="change-1"' not in after and 'id="change-2"' in after
    assert (notice(client.get(LIST).text) or "").startswith(
        DE["filaments.price.notice"].replace("{n}", "1")
    )


def test_21_an_overview_without_changes_says_so(client: AppClient) -> None:
    linked(client, spool(1, cost_per_kg=20))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    assert DE["filaments.changes.empty"] in client.get(OVERVIEW).text


def test_22_a_reader_sees_the_overview_without_the_button(
    client: AppClient, reader: AppClient
) -> None:
    linked(client, spool(1, cost_per_kg=25))
    fid = one_filament(client)
    assign(fid, 1, price="20")
    reader.app.state.bambuddy_transport = client.app.state.bambuddy_transport
    html = reader.get(OVERVIEW).text
    assert 'id="change-1"' in html and "/filaments/spools/1/price" not in html
    assert reader.post("/filaments/spools/1/price", follow_redirects=False).status_code == 403
    assert assignments_price(1) == 20


def test_22_the_overview_needs_a_login(client: AppClient) -> None:
    with AppClient(client.app) as anonymous:
        assert anonymous.get(OVERVIEW, follow_redirects=False).status_code in (303, 401, 403)


# --- Case 23: one request per page view -------------------------------------------------------


def test_23_each_page_view_asks_bambuddy_exactly_once(client: AppClient) -> None:
    bambuddy = linked(client, spool(1, cost_per_kg=25))
    fid = one_filament(client)
    assign(fid, 1, price="20")

    def asked() -> int:
        return sum(1 for r in bambuddy.requests if r.url.path.endswith("/inventory/spools"))

    for address in (LIST, OVERVIEW, f"/filaments/{fid}/spools"):
        bambuddy.requests.clear()
        client.get(address)
        assert asked() == 1, address


def test_texts_exist_in_both_languages() -> None:
    for key in (
        "filaments.price.chip",
        "filaments.price.notice",
        "filaments.price.notice.link",
        "filaments.changes.title",
        "filaments.changes.intro",
        "filaments.changes.empty",
    ):
        assert DE.get(key) and EN.get(key), key
