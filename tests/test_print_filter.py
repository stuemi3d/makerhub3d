"""Page Production: filter bar in the views open, assigned and ignored (issue #276).
All data is invented; Bambuddy answers come from a mock. The numbers refer to the test cases
in the comment on the issue; A and B are the two decisions (same behaviour as the articles)."""

import html as html_lib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app import bambuddy
from tests.auth import READER_NAME, AppClient, add_user, client_as, signed_in_client
from tests.machine import add_printer
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_print_printer import book
from tests.test_production_page import ignore, names, prepared, raw

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))

ARCHIVES = [
    raw(5, "2026-11-13T09:00:00", name="Wandhalter groß", printer_id=1),
    raw(4, "2026-11-12T23:30:00", name="Kabel 100% Clip", printer_id=2),
    raw(3, "2026-11-11T08:00:00", name="HALTER klein", printer_id=2),
    raw(2, "2026-11-10T00:10:00", name="Deckel", printer_id=1),
    raw(1, "2026-11-09T12:00:00", name="Fremder Druck", printer_id=None),
]


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        add_printer("1", bambuddy_printer_id=1, name="Drucker Links")
        add_printer("1", bambuddy_printer_id=2, name="Drucker Rechts")
        prepared(test_client, ARCHIVES)
        yield test_client


def shown(client: AppClient, query: str) -> list[str]:
    return names(client.get(f"/production?{query}").text)


def bar(page: str) -> str:
    match = re.search(r"<details class=\"filter-bar\".*?</details>", page, re.S)
    assert match
    return match.group(0)


def field_names(page: str) -> list[str]:
    return re.findall(r'<(?:input|select) (?:type="(?:search|date)" )?name="([^"]+)"', bar(page))


def choices(page: str, name: str) -> list[str]:
    select = re.search(rf'<select name="{name}".*?</select>', bar(page), re.S)
    assert select
    return [html_lib.unescape(text) for text in re.findall(r">([^<]*)</option>", select.group(0))]


def hidden(page: str) -> dict[str, str]:
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', bar(page)))


def query_of(href: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(html_lib.unescape(href)).query)


# --- Cases 1-4: fields and choices -------------------------------------------------------------


@pytest.mark.parametrize("view", ["open", "ignored"])
def test_open_and_ignored_have_date_printer_and_search(client: AppClient, view: str) -> None:
    if view == "ignored":
        assert ignore(client, 5).status_code == 303
    page = client.get(f"/production?view={view}").text
    assert set(field_names(page)) == {"from", "to", "printer", "q"}


def test_assigned_also_has_the_article(client: AppClient) -> None:
    book(5, None)
    page = client.get("/production?view=assigned").text
    assert set(field_names(page)) == {"from", "to", "printer", "q", "article"}


def test_the_printer_choices_are_those_of_the_view(client: AppClient) -> None:
    for archive_id in (3, 4):
        assert ignore(client, archive_id).status_code == 303
    assert choices(client.get("/production?view=ignored").text, "printer") == [
        "Alle Drucker",
        "Drucker Rechts",
    ]
    assert choices(client.get("/production").text, "printer") == [
        "Alle Drucker",
        "Drucker Links",
    ]


def test_the_article_choices_are_the_booked_articles(client: AppClient) -> None:
    book(5, None)
    book(3, None)
    assert choices(client.get("/production?view=assigned").text, "article") == [
        "Alle Artikel",
        "3D-0003 · Kabelhalter",
        "3D-0005 · Kabelhalter",
    ]


# --- Cases 5-11: filtering ---------------------------------------------------------------------


def test_the_period_includes_both_days(client: AppClient) -> None:
    assert shown(client, "from=2026-11-10&to=2026-11-12") == [
        "Kabel 100% Clip",
        "HALTER klein",
        "Deckel",
    ]
    assert shown(client, "from=2026-11-13") == ["Wandhalter groß"]
    assert shown(client, "to=2026-11-09") == ["Fremder Druck"]


def test_the_printer_filter_uses_the_shown_name(client: AppClient) -> None:
    assert shown(client, "printer=Drucker+Links") == ["Wandhalter groß", "Deckel"]


def test_the_search_ignores_case_and_takes_special_characters_literally(
    client: AppClient,
) -> None:
    assert shown(client, "q=halter") == ["Wandhalter groß", "HALTER klein"]
    assert shown(client, "q=100%25") == ["Kabel 100% Clip"]
    assert shown(client, "q=_") == []


def test_the_article_filter_keeps_the_prints_of_that_article(client: AppClient) -> None:
    book(5, None)
    book(3, None)
    page = client.get("/production?view=assigned").text
    article = re.search(r'<option value="(\d+)">3D-0003', bar(page)).group(1)
    assert shown(client, f"view=assigned&article={article}") == ["HALTER klein"]


def test_a_cancelled_production_gives_no_article_choice(client: AppClient) -> None:
    from sqlalchemy import func, select

    from app import production
    from tests.test_stock import db

    book(5, None)
    with db() as session, session.begin():
        production_id = session.scalar(select(func.max(production.Production.id)))
        assert production.cancel(session, None, production_id) is None
    book(3, None)
    assert choices(client.get("/production?view=assigned").text, "article") == [
        "Alle Artikel",
        "3D-0003 · Kabelhalter",
    ]


def test_several_filters_must_all_match(client: AppClient) -> None:
    query = "printer=Drucker+Rechts&q=halter&from=2026-11-10"
    assert shown(client, query) == ["HALTER klein"]


def test_no_match_says_so_and_not_the_empty_text_of_the_view(client: AppClient) -> None:
    page = client.get("/production?q=gibtesnicht").text
    assert DE["filter.no_results"] in page
    assert DE["production.empty.open"] not in page


@pytest.mark.parametrize(
    "query",
    ["printer=Erfundener+Drucker", "from=2026-02-30", "to=gestern", "article=abc"],
)
def test_nonsense_in_the_address_is_ignored(client: AppClient, query: str) -> None:
    response = client.get(f"/production?{query}")
    assert response.status_code == 200
    assert len(names(response.text)) == 5


def test_an_unknown_article_in_the_assigned_view_is_ignored(client: AppClient) -> None:
    book(5, None)
    assert shown(client, "view=assigned&article=99999") == ["Wandhalter groß"]


# --- Cases 12-13: view and sorting stay --------------------------------------------------------


def test_filtering_keeps_the_view_and_the_sorting(client: AppClient) -> None:
    for archive_id in (2, 3, 4):
        assert ignore(client, archive_id).status_code == 303
    page = client.get("/production?view=ignored&sort=name&dir=asc&printer=Drucker+Rechts").text
    assert names(page) == ["HALTER klein", "Kabel 100% Clip"]
    assert hidden(page) == {"view": "ignored", "sort": "name", "dir": "asc"}
    head = re.search(r"<thead>.*?</thead>", page, re.S).group(0)
    for href in re.findall(r'href="([^"]+)"', head):
        query = query_of(href)
        assert query["view"] == ["ignored"]
        assert query["printer"] == ["Drucker Rechts"]


def test_chips_remove_one_filter_and_reset_removes_all(client: AppClient) -> None:
    page = client.get("/production?view=open&sort=name&dir=asc&printer=Drucker+Links&q=deckel").text
    chips = re.findall(r'<li class="filter-chip">.*?<a href="([^"]+)"', page, re.S)
    assert len(chips) == 2
    for href in chips:
        query = query_of(href)
        assert query["view"] == ["open"]
        assert query["sort"] == ["name"]
        assert len({"printer", "q"} & set(query)) == 1
    reset = re.search(rf'<a href="([^"]+)">{DE["filter.reset"]}</a>', page).group(1)
    assert query_of(reset) == {"view": ["open"], "sort": ["name"], "dir": ["asc"]}


def test_switching_the_view_starts_without_filter(client: AppClient) -> None:
    """Decision A: like the tab "Productions", the tabs are plain addresses."""
    page = client.get("/production?printer=Drucker+Links").text
    tabs = re.search(r'<nav [^>]*class="area-tabs".*?</nav>', page, re.S).group(0)
    assert 'href="/production?view=ignored"' in tabs
    assert "printer" not in tabs


@pytest.mark.parametrize("action", ["ignore", "unignore"])
def test_ignoring_keeps_filter_and_sorting(client: AppClient, action: str) -> None:
    """Decision B: like the articles, the list comes back as it was."""
    view = "open"
    if action == "unignore":
        assert ignore(client, 5).status_code == 303
        view = "ignored"
    page = client.get(f"/production?view={view}&sort=name&dir=asc&q=halter").text
    form = re.search(
        rf'<form class="icon-form" method="post" action="/production/{action}">.*?</form>',
        page,
        re.S,
    )
    sent = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(0)))
    sent["archive_id"] = "5"
    response = client.post(f"/production/{action}", data=sent, follow_redirects=False)
    assert response.status_code == 303
    assert query_of(response.headers["location"]) == {
        "view": [view],
        "sort": ["name"],
        "dir": ["asc"],
        "q": ["halter"],
    }


def test_the_way_back_after_ignoring_never_leaves_the_page(client: AppClient) -> None:
    sent = {
        "archive_id": "5",
        "view": "open",
        "q": "x" * 300,
        "sort": "evil",
        "from": "https://example.com",
        "printer": "a&view=ignored",
    }
    response = ignore_with(client, sent)
    target = response.headers["location"]
    assert target.startswith("/production?")
    query = query_of(target)
    assert query["view"] == ["open"]
    assert query["q"] == ["x" * 100]
    assert query["printer"] == ["a&view=ignored"]
    assert "sort" not in query and "from" not in query


def ignore_with(client: AppClient, data: dict[str, str]):
    return client.post("/production/ignore", data=data, follow_redirects=False)


# --- Cases 14-17 -------------------------------------------------------------------------------


def test_a_cut_list_keeps_its_notice_with_a_filter(
    client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bambuddy, "MAX_ARCHIVES", 2)
    page = client.get("/production?q=halter").text
    assert DE["production.truncated"] in page
    assert names(page) == ["Wandhalter groß"]


def test_without_bambuddy_there_is_no_filter_bar() -> None:
    with signed_in_client() as fresh:
        page = fresh.get("/production").text
    assert "filter-bar" not in page


def test_choosing_works_with_a_filter(client: AppClient) -> None:
    page = client.get("/production?q=halter").text
    assert re.findall(r'name="prints" value="(\d+)"', page) == ["5", "3"]
    assert 'id="select-form"' in page
    # Even when nothing matches, the form and the button stay for a later filter
    empty = client.get("/production?q=gibtesnicht").text
    assert 'id="select-form"' in empty
    assert "data-requires-checked" in empty


def test_readers_see_the_filter_too(client: AppClient) -> None:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        reader.app.state.bambuddy_transport = client.app.state.bambuddy_transport
        page = reader.get("/production?q=deckel").text
    assert "filter-bar" in page
    assert names(page) == ["Deckel"]


def test_the_dashboard_has_no_filter(client: AppClient) -> None:
    assert "filter-bar" not in client.get("/").text
