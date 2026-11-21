"""The spools of Bambuddy for the calculation page (issue #161). Read-only.

Problems come back as data (a translation key), never as an exception, so that the page stays
usable when Bambuddy is missing.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2
from sqlalchemy.orm import Session

from app.bambuddy import BambuddyClient, BambuddyError, Spool
from app.models import BambuddyConnection
from app.two_factor import Cipher

SPOOLS_TIMEOUT = 5.0


@dataclass(frozen=True)
class SpoolList:
    spools: list[Spool] = field(default_factory=list)
    # The translation key of why there are no spools, or None
    problem: str | None = None


def load(
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    transport: httpx2.BaseTransport | None,
    include_archived: bool = False,
) -> SpoolList:
    with new_session() as db:
        row = db.get(BambuddyConnection, 1)
        url, token = row.url, row.api_key_encrypted
    if not url or not token:
        return SpoolList(problem="calc.spools.not_set_up")
    api_key = cipher.decrypt(token) if cipher is not None else None
    if api_key is None:
        return SpoolList(problem="bambuddy.error.key_unreadable")
    try:
        client = BambuddyClient(url, api_key, transport=transport, timeout=SPOOLS_TIMEOUT)
        return SpoolList(client.spools(include_archived=include_archived))
    except BambuddyError as error:
        return SpoolList(problem=f"bambuddy.error.{error.code}")


CACHE_SECONDS = 90.0


class SpoolCache:
    """Keeps a successful answer for a short time, so that pages stay fast with many spools.
    A problem is never kept: the next call asks again."""

    def __init__(self, ttl: float = CACHE_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl
        self._clock = clock
        self._kept: dict[object, tuple[float, SpoolList]] = {}

    def get(self, key: object, fetch: Callable[[], SpoolList]) -> SpoolList:
        kept = self._kept.get(key)
        if kept is not None and self._clock() - kept[0] < self._ttl:
            return kept[1]
        found = fetch()
        if found.problem:
            self._kept.pop(key, None)
        else:
            self._kept[key] = (self._clock(), found)
        return found


def load_cached(
    cache: SpoolCache,
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    transport: httpx2.BaseTransport | None,
    include_archived: bool = False,
) -> SpoolList:
    """Like `load`, but a fresh answer is reused. The key includes the connection, so a changed
    address or key is never answered from the old connection's spools."""
    with new_session() as db:
        row = db.get(BambuddyConnection, 1)
        key = (row.url, row.api_key_encrypted, include_archived)
    return cache.get(key, lambda: load(new_session, cipher, transport, include_archived))
