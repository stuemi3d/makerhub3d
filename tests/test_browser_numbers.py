"""The shared hand-out of example numbers for browser tests (issue #252).

The numbers are only handed out here, never chosen by hand, so two tests cannot take the same
number from the shared browser database. The test case numbers refer to the confirmed list in
the issue. Case 4 (the spool tests run in any order) is checked by running them, case 6 is the
note in CLAUDE.md.
"""

from pathlib import Path

import pytest

from tests.browser import numbers

REPO_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def fresh() -> None:
    numbers.reset()


def test_1_two_requests_give_two_different_numbers() -> None:
    assert numbers.spool_number("a", "desktop", 0) != numbers.spool_number("a", "desktop", 1)


def test_2_different_tests_and_viewports_never_share_a_number() -> None:
    taken = [
        numbers.spool_number(owner, viewport, index)
        for owner in ("tests.browser.test_a", "tests.browser.test_b")
        for viewport in ("desktop", "phone")
        for index in range(3)
    ]
    assert len(set(taken)) == len(taken)


def test_3_the_same_request_gives_the_same_number_again() -> None:
    first = numbers.spool_number("a", "phone", 2)
    numbers.spool_number("b", "phone", 2)
    assert numbers.spool_number("a", "phone", 2) == first


def test_the_order_of_requests_does_not_matter_for_uniqueness() -> None:
    forward = [numbers.spool_number(o, "desktop") for o in "abc"]
    numbers.reset()
    backward = [numbers.spool_number(o, "desktop") for o in "cba"]
    assert len(set(forward)) == len(set(backward)) == 3


def test_5_serving_the_same_spool_twice_gives_a_clear_message() -> None:
    with pytest.raises(AssertionError, match=r"spool number 1001 .* twice"):
        numbers.check_unique([{"id": 1001}, {"id": 1002}, {"id": 1001}])
    numbers.check_unique([{"id": 1001}, {"id": 1002}])


def test_numbers_start_above_the_range_old_tests_used_by_hand() -> None:
    assert numbers.spool_number("a", "desktop") >= 1000


def test_6_claude_md_tells_to_use_the_hand_out() -> None:
    text = (REPO_DIR / "CLAUDE.md").read_text(encoding="utf-8")
    assert "tests/browser/numbers.py" in text
