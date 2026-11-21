"""Filaments, part 6: details of the assigned spools (issue #248). All data is invented.

The test case numbers refer to the confirmed list in the issue. Case 9 (phone width) is in
tests/browser/test_spool_details.py. Bambuddy answers come from a mock.
"""

import json
import re
from pathlib import Path

import httpx2
import pytest

from tests.auth import AppClient
from tests.test_filament_stock import NOW, assign, one_filament
from tests.test_spool_assignment import linked, spool
from tests.test_spool_follow import (  # noqa: F401
    ARCHIVED,
    reader,
    row,
    secret_key,
    spools_page,
    text,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EMPTY = DE["table.empty_value"]


def cell(html: str, spool_id: int, hook: str) -> str:
    found = re.search(rf'<td class="[^"]*cell-{hook}[^"]*">(.*?)</td>', row(html, spool_id), re.S)
    assert found, f"no {hook} cell for spool {spool_id}"
    return text(found.group(1))


def head_texts(html: str) -> list[str]:
    return [text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)]


def test_1_the_page_shows_the_detail_columns_for_each_spool(client: AppClient) -> None:
    linked(
        client,
        spool(
            1,
            last_dried_at="2026-10-01T08:00:00",
            last_used="2026-11-05T17:30:00",
            note="Im Karton links",
        ),
    )
    fid = one_filament(client)
    assign(fid, 1)
    html = client.get(spools_page(fid)).text
    for key in ("remaining", "assigned", "dried", "used", "note"):
        assert DE[f"filaments.assigned.col.{key}"] in head_texts(html)
    assert cell(html, 1, "dried") == "01.10.2026"
    assert cell(html, 1, "used") == "05.11.2026"
    assert cell(html, 1, "note") == "Im Karton links"


def test_2_remaining_is_nominal_minus_used_and_comes_live(client: AppClient) -> None:
    bambuddy = linked(client, spool(1, label_weight=750, weight_used=434))
    fid = one_filament(client)
    assign(fid, 1, weight="750")
    assert cell(client.get(spools_page(fid)).text, 1, "remaining") == "316"
    bambuddy.spools[0]["weight_used"] = 500
    assert cell(client.get(spools_page(fid)).text, 1, "remaining") == "250"


def test_2b_an_overused_spool_shows_zero_not_a_negative_number(client: AppClient) -> None:
    linked(client, spool(1, label_weight=1000, weight_used=1010))
    fid = one_filament(client)
    assign(fid, 1)
    assert cell(client.get(spools_page(fid)).text, 1, "remaining") == "0"


def test_3_the_assignment_day_is_shown(client: AppClient) -> None:
    linked(client, spool(1))
    fid = one_filament(client)
    assign(fid, 1)
    assert cell(client.get(spools_page(fid)).text, 1, "assigned") == NOW.strftime("%d.%m.%Y")


def test_4_missing_values_show_the_placeholder(client: AppClient) -> None:
    linked(client, spool(1))
    fid = one_filament(client)
    assign(fid, 1)
    html = client.get(spools_page(fid)).text
    for hook in ("dried", "used", "note"):
        assert cell(html, 1, hook) == EMPTY


def test_5_the_storage_column_only_exists_when_a_spool_has_a_location(
    client: AppClient,
) -> None:
    bambuddy = linked(client, spool(1), spool(2))
    fid = one_filament(client)
    assign(fid, 1)
    assign(fid, 2)
    html = client.get(spools_page(fid)).text
    assert DE["filaments.assigned.col.location"] not in head_texts(html)
    bambuddy.spools[0]["storage_location"] = "Regal 3"
    html = client.get(spools_page(fid)).text
    assert DE["filaments.assigned.col.location"] in head_texts(html)
    assert cell(html, 1, "location") == "Regal 3"
    assert cell(html, 2, "location") == EMPTY


def test_6_texts_from_bambuddy_are_escaped_and_long_notes_can_wrap(client: AppClient) -> None:
    linked(client, spool(1, note="<script>alert(1)</script> " + "lang " * 80))
    fid = one_filament(client)
    assign(fid, 1)
    html = client.get(spools_page(fid)).text
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html
    assert 'class="cell-note' in row(html, 1)


def test_7_when_bambuddy_cannot_be_read_the_stored_values_stay(client: AppClient) -> None:
    linked(client, spool(1))
    fid = one_filament(client)
    assign(fid, 1, price="20", weight="1000")
    client.app.state.bambuddy_transport = httpx2.MockTransport(lambda r: httpx2.Response(500))
    response = client.get(spools_page(fid))
    assert response.status_code == 200
    assert DE["filaments.spools.unreadable"] in response.text
    assert "20,00" in row(response.text, 1)
    assert cell(response.text, 1, "assigned") == NOW.strftime("%d.%m.%Y")
    for hook in ("remaining", "dried", "used", "note"):
        assert cell(response.text, 1, hook) == EMPTY


def test_8_archived_and_deleted_spools_keep_state_and_stored_values(
    client: AppClient,
) -> None:
    linked(client, spool(1, archived_at=ARCHIVED, note="alt"), spool(3))
    fid = one_filament(client)
    assign(fid, 1)
    assign(fid, 2, price="21", weight="1000")
    html = client.get(spools_page(fid)).text
    assert DE["filaments.state.archived"] in row(html, 1)
    assert cell(html, 1, "note") == "alt"
    assert DE["filaments.state.gone"] in row(html, 2)
    assert "21,00" in row(html, 2)
    for hook in ("remaining", "dried", "used", "note"):
        assert cell(html, 2, hook) == EMPTY
    assert cell(html, 2, "assigned") == NOW.strftime("%d.%m.%Y")


def test_10_actions_and_rights_stay_as_they_were(client: AppClient, reader: AppClient) -> None:  # noqa: F811
    linked(client, spool(1))
    fid = one_filament(client)
    assign(fid, 1)
    assert "/filaments/spools/1/release" in client.get(spools_page(fid)).text
    reader.app.state.bambuddy_transport = client.app.state.bambuddy_transport
    html = reader.get(spools_page(fid)).text
    assert "/filaments/spools/1/release" not in html
    assert cell(html, 1, "remaining") != EMPTY


@pytest.mark.parametrize("key", ["remaining", "assigned", "dried", "used", "note", "location"])
def test_translations_exist_in_both_languages(key: str) -> None:
    en = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
    assert DE[f"filaments.assigned.col.{key}"] and en[f"filaments.assigned.col.{key}"]


def test_a_note_or_location_of_only_spaces_counts_as_empty(client: AppClient) -> None:
    linked(client, spool(1, note="   ", storage_location="  "))
    fid = one_filament(client)
    assign(fid, 1)
    html = client.get(spools_page(fid)).text
    assert cell(html, 1, "note") == EMPTY
    assert DE["filaments.assigned.col.location"] not in head_texts(html)
