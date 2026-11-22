"""Example numbers for browser tests, handed out once (issue #252).

All browser tests share one database. Choosing numbers by hand lets two tests take the same
one, which only shows in the full run. Ask here instead: every (owner, viewport, index) gets
its own number, and asking again gives the same number. Use `__name__` as the owner.
"""

from itertools import count

FIRST_SPOOL_NUMBER = 1000

_taken: dict[tuple[str, str, int], int] = {}
_counter = count(FIRST_SPOOL_NUMBER)


def reset() -> None:
    """Forget everything handed out (for the tests of this module)."""
    global _counter
    _taken.clear()
    _counter = count(FIRST_SPOOL_NUMBER)


def spool_number(owner: str, viewport: str, index: int = 0) -> int:
    """The spool number of one test, for one viewport; `index` for several spools per test."""
    key = (owner, viewport, index)
    if key not in _taken:
        _taken[key] = next(_counter)
    return _taken[key]


def check_unique(spools: list[dict]) -> None:
    """Fail with a clear message when a list of spools has a number twice."""
    seen: set[int] = set()
    for spool in spools:
        number = spool["id"]
        assert number not in seen, (
            f"spool number {number} is served twice: take numbers from tests/browser/numbers.py"
        )
        seen.add(number)
