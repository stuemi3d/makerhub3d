"""Production form: order of the blocks, hints per print, short intro (issue #146). All data
is invented; Bambuddy answers come from a mock. The test case numbers refer to the comment on
the issue.
"""

import json
import re
from pathlib import Path

import pytest

from tests.auth import AppClient
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_booking import form, send, setup_prints
from tests.test_stock import add_article

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def prepared_client(client: AppClient) -> AppClient:
    setup_prints(client)
    return client


def preview_html(client: AppClient) -> str:
    article_id = add_article()
    return send(client, form([6], (article_id, "5"), action="preview")).text


# --- Cases 1-3: order and style ----------------------------------------------------------------


def test_the_blocks_come_in_the_new_order(prepared_client: AppClient) -> None:
    html = preview_html(prepared_client)

    order = [
        html.index('class="form-block print-block'),
        html.index('class="form-block labor-block'),
        html.index('class="form-block result-block'),
        html.index('class="preview-total"'),
        html.index('class="form-block date-block'),
        html.index('class="form-actions form-actions-pinned"'),
    ]
    assert order == sorted(order)


def test_the_cost_per_piece_is_in_the_result_block_and_the_total_follows(
    prepared_client: AppClient,
) -> None:
    html = preview_html(prepared_client)

    result = html.split('class="form-block result-block')[1].split('class="preview-total"')[0]
    assert DE["production.col.per_piece"] in result
    assert DE["production.preview.total"] not in result
    assert DE["production.preview.total"] in html.split('class="preview-total"')[1]


def test_date_and_note_use_the_same_block_style(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/new?prints=6").text

    assert 'class="form-block date-block' in html
    assert "detail-card" not in html.split("<form")[1]
    block = html.split('class="form-block date-block')[1].split("</div>")[0]
    assert 'name="date"' in block and 'name="note"' in block


# --- Cases 4-5: hints --------------------------------------------------------------------------


def test_the_duration_is_in_the_head_of_the_print(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/new?prints=6").text

    block = html.split('class="form-block print-block')[1].split("<h2")[0]
    head = block.split('class="field-grid"')[0]
    assert "16 min" in head  # print 6 lasted 1000 s
    assert DE["production.col.duration"] + ":" not in block


def test_hints_under_fields_have_their_own_style() -> None:
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")

    assert re.search(r"\.field-grid small,[^{]*\{[^}]*margin-top: 0", css)


# --- Case 6: missing costs at the print --------------------------------------------------------


def test_the_missing_cost_hint_is_at_the_print_with_missing_costs(
    prepared_client: AppClient,
) -> None:
    html = prepared_client.get("/production/new?prints=5&prints=6").text

    blocks = [part.split("<h2")[0] for part in html.split('class="form-block print-block')[1:]]
    assert len(blocks) == 2
    five, six = blocks  # the order of the form follows the order in the address
    assert DE["production.cost_missing"] in five
    assert DE["production.cost_missing"] not in six
    assert html.count(DE["production.cost_missing"]) == 1


def test_no_hint_when_all_costs_are_there(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/new?prints=6&prints=7").text

    assert DE["production.cost_missing"] not in html


# --- Cases 7-8: buttons and texts --------------------------------------------------------------


def test_the_buttons_are_pinned_and_preview_stays_the_first_button() -> None:
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")

    assert re.search(r"\.form-actions-pinned\s*\{[^}]*position: sticky", css)


def test_the_first_button_is_preview(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/new?prints=6").text

    actions = html.split('class="form-actions')[1]
    assert 'class="form-actions form-actions-pinned"' in html
    assert actions.index('value="preview"') < actions.index('value="book"')


@pytest.mark.parametrize("texts", [DE, EN])
def test_the_intro_has_at_most_two_sentences(texts: dict[str, str]) -> None:
    intro = texts["production.new.intro"]

    sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", intro.strip())
    assert 1 <= len(sentences) <= 2, sentences
