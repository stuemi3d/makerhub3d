"""Production form and the create button: structure after the layout change (issue #132,
test cases 1-8). All data is invented; Bambuddy answers come from a mock. The test case
numbers refer to the comment on the issue.
"""

import json
import re
from pathlib import Path

import pytest

from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_production_booking import ARCHIVES, form, send, setup_prints
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


# --- Cases 1-4: the button ---------------------------------------------------------------------


def test_the_create_button_waits_for_a_chosen_print_and_counts(
    prepared_client: AppClient,
) -> None:
    html = prepared_client.get("/production").text

    button = re.search(r"<button\b[^>]*data-requires-checked[^>]*>.*?</button>", html, re.S)
    assert button and DE["production.create"] in button.group(0)
    assert 'form="select-form"' in button.group(0)
    # The server does not rely on the script: the button is not disabled in the page itself.
    assert " disabled" not in button.group(0)
    count = re.search(r"<span\b[^>]*data-selected-text=\"([^\"]*)\"", html)
    assert count and count.group(1) == DE["production.selected"]


def test_the_button_sits_in_the_head_and_the_hint_above_the_list(
    prepared_client: AppClient,
) -> None:
    html = prepared_client.get("/production").text

    head = re.search(r'<div class="page-head">.*?</div>', html, re.S).group(0)
    assert "page-actions" in html
    actions = re.search(r'<div class="page-actions">.*?</div>', html, re.S).group(0)
    assert "data-requires-checked" in actions
    assert (
        html.index('class="area-tabs"')
        < html.index("page-actions")
        < html.index('class="data-card"')
    )
    # The hint is plain text between the tabs and the table, not next to the button.
    hint = html.index(DE["production.create.hint"])
    assert html.index('class="area-tabs"') < hint < html.index('class="data-card"')
    assert DE["production.create.hint"] not in actions
    assert head


def test_the_button_is_only_there_for_editors_and_when_there_are_prints(
    prepared_client: AppClient,
) -> None:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        reader.app.state.bambuddy_transport = prepared_client.app.state.bambuddy_transport
        assert "data-requires-checked" not in reader.get("/production").text
    assert "data-requires-checked" not in prepared_client.get("/production?view=ignored").text


def test_without_a_print_the_server_still_says_so(prepared_client: AppClient) -> None:
    response = prepared_client.get("/production/new")

    assert response.status_code == 422
    assert DE["production.error.no_print"] in response.text


# --- Cases 5-6: blocks instead of a wide table -------------------------------------------------


def test_the_form_uses_labelled_blocks_and_no_table(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/new?prints=6&prints=7").text

    assert "<table" not in html
    prints = [part.split("<h2")[0] for part in html.split('<div class="form-block print-block')[1:]]
    assert len(prints) == 2
    for block in prints:
        for part in ("material", "electricity", "wear"):
            assert re.search(rf'<label for="cost_{part}_\d+">', block)
    rows = re.findall(r'<div class="form-block result-block', html)
    assert len(rows) == 3
    for number in (1, 2, 3):
        assert f'<label for="article_{number}">' in html
        assert f'<label for="quantity_{number}">' in html
        assert f'<label for="weight_{number}">' in html


def test_a_failed_print_is_marked_in_the_form(prepared_client: AppClient) -> None:
    html = prepared_client.get("/production/new?prints=6&prints=8").text

    blocks = [part.split("<h2")[0] for part in html.split('<div class="form-block print-block')[1:]]
    good, failed = blocks
    assert "print-failed" not in good.split(">")[0]
    assert "print-failed" in failed.split(">")[0]
    assert DE["production.status.failed"] in failed
    assert DE["production.status.failed"] not in good
    css = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".print-block.print-failed" in css


def test_the_blocks_get_the_preview_fields_after_the_preview(prepared_client: AppClient) -> None:
    article_id = add_article()

    html = send(prepared_client, form([6, 7], [(article_id, "5")], action="preview")).text

    assert "<table" not in html
    assert '<label for="alloc_1">' in html
    assert DE["production.col.per_piece"] in html
    assert re.search(r'name="alloc_1"[^>]*value="7,25"', html)


def test_the_labels_are_the_old_column_names() -> None:
    for key in ("production.col.article", "production.col.quantity", "production.col.weight"):
        assert DE[key] and EN[key]
    assert DE["production.selected"] and EN["production.selected"]
    assert ARCHIVES  # the same prints as in the booking tests
