"""The shared filter bar logic (issue #257): reading the address and building the bar."""

from app import list_filter
from app.list_filter import Field

FIELDS = (
    Field("q", "f.search"),
    Field("status", "f.status", (("active", "f.active"), ("inactive", "f.inactive")), "active"),
)


WORDS = {"f.search": "Suche", "f.status": "Status", "f.active": "aktiv", "f.inactive": "inaktiv"}


def words(key: str) -> str:
    return WORDS[key]


def test_read_takes_valid_values_and_defaults() -> None:
    values = list_filter.read(FIELDS, {"q": "  Würfel ", "status": "inactive"})
    assert values == {"q": "Würfel", "status": "inactive"}
    assert list_filter.read(FIELDS, {}) == {"q": "", "status": "active"}


def test_read_ignores_unknown_choices_and_cuts_long_text() -> None:
    values = list_filter.read(FIELDS, {"status": "bogus", "q": "x" * 500})
    assert values["status"] == "active"
    assert len(values["q"]) == list_filter.MAX_TEXT


def test_params_hold_only_non_default_values() -> None:
    assert list_filter.params(FIELDS, {"q": "", "status": "active"}) == {}
    assert list_filter.params(FIELDS, {"q": "a b", "status": "inactive"}) == {
        "q": "a b",
        "status": "inactive",
    }


def test_bar_has_chips_with_removal_links_and_reset() -> None:
    bar = list_filter.build("/articles", FIELDS, {"q": "Cube", "status": "inactive"}, words)
    assert bar["active_count"] == 2
    chips = {chip["name"]: chip for chip in bar["chips"]}
    assert chips["q"]["text"] == "Suche: Cube"
    assert chips["status"]["text"] == "Status: inaktiv"
    # Removing one keeps the other
    assert chips["q"]["href"] == "/articles?status=inactive"
    assert chips["status"]["href"] == "/articles?q=Cube"
    assert bar["reset_href"] == "/articles"


def test_bar_without_filters_has_no_chips() -> None:
    bar = list_filter.build("/articles", FIELDS, {"q": "", "status": "active"}, words)
    assert bar["chips"] == [] and bar["active_count"] == 0


def test_links_keep_the_sort() -> None:
    keep = {"sort": "name", "dir": "desc"}
    bar = list_filter.build("/articles", FIELDS, {"q": "Cube", "status": "active"}, words, keep)
    assert bar["chips"][0]["href"] == "/articles?sort=name&dir=desc"
    assert bar["reset_href"] == "/articles?sort=name&dir=desc"
    assert bar["hidden"] == keep


def test_text_matches_any_part_case_blind() -> None:
    assert list_filter.matches("Würfel", "3D-0001", "Würfel klein")
    assert list_filter.matches("0001", "3D-0001", "x")
    assert not list_filter.matches("abc", "3D-0001", "x")
    # No wildcard meaning for % and _
    assert not list_filter.matches("%", "3D-0001", "x")
    assert list_filter.matches("", "a")
