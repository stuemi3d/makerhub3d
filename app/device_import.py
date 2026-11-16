"""The printers Bambuddy knows and that can be taken over as a device (issue #163).

Read only. Problems come back as data, never as an exception, so that the form stays usable.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bambuddy import BambuddyClient, BambuddyError, Printer
from app.models import BambuddyConnection, Device
from app.two_factor import Cipher


@dataclass
class Choice:
    """The Bambuddy printers not yet taken over, or why there are none to offer."""

    printers: list[Printer] = field(default_factory=list)
    not_set_up: bool = False
    # Translation key of the problem, if Bambuddy could not be read
    error: str | None = None

    def find(self, printer_id: int) -> Printer | None:
        return next((printer for printer in self.printers if printer.id == printer_id), None)


def load(
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    transport: httpx2.BaseTransport | None,
) -> Choice:
    with new_session() as db:
        row = db.get(BambuddyConnection, 1)
        url, token = (row.url, row.api_key_encrypted) if row else (None, None)
        taken = set(db.scalars(select(Device.bambuddy_printer_id)))
    if not url or not token:
        return Choice(not_set_up=True)
    api_key = cipher.decrypt(token) if cipher is not None else None
    if api_key is None:
        return Choice(error="bambuddy.error.key_unreadable")
    try:
        printers = BambuddyClient(url, api_key, transport=transport).printers()
    except BambuddyError as error:
        return Choice(error=f"bambuddy.error.{error.code}")
    return Choice([printer for printer in printers if printer.id not in taken])
