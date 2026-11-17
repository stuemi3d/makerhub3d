"""The printers Bambuddy knows and that can be taken over as a device (issue #163).

Read only. Problems come back as data, never as an exception, so that the form stays usable.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bambuddy import BambuddyClient, BambuddyError, Printer
from app.models import BambuddyConnection, Device, PrinterModel
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


def _read_printers(
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    transport: httpx2.BaseTransport | None,
) -> tuple[list[Printer], Choice | None]:
    """All printers of Bambuddy, or a Choice that tells why they could not be read."""
    with new_session() as db:
        row = db.get(BambuddyConnection, 1)
        url, token = (row.url, row.api_key_encrypted) if row else (None, None)
    if not url or not token:
        return [], Choice(not_set_up=True)
    api_key = cipher.decrypt(token) if cipher is not None else None
    if api_key is None:
        return [], Choice(error="bambuddy.error.key_unreadable")
    try:
        return BambuddyClient(url, api_key, transport=transport).printers(), None
    except BambuddyError as error:
        return [], Choice(error=f"bambuddy.error.{error.code}")


def load(
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    transport: httpx2.BaseTransport | None,
) -> Choice:
    printers, problem = _read_printers(new_session, cipher, transport)
    if problem is not None:
        return problem
    with new_session() as db:
        taken = set(db.scalars(select(Device.bambuddy_printer_id)))
    return Choice([printer for printer in printers if printer.id not in taken])


@dataclass
class ModelChoice:
    """The printer models Bambuddy reports that are not created here yet (issue #174)."""

    models: list[str] = field(default_factory=list)
    not_set_up: bool = False
    # Translation key of the problem, if Bambuddy could not be read
    error: str | None = None
    # True when Bambuddy answered but knows no printer with a model
    no_models: bool = False

    def find(self, name: str) -> str | None:
        wanted = name.strip().casefold()
        return next((model for model in self.models if model.casefold() == wanted), None)


def load_models(
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    transport: httpx2.BaseTransport | None,
) -> ModelChoice:
    printers, problem = _read_printers(new_session, cipher, transport)
    if problem is not None:
        return ModelChoice(not_set_up=problem.not_set_up, error=problem.error)
    reported: dict[str, str] = {}
    for printer in printers:
        if printer.model and printer.model.strip():
            reported.setdefault(printer.model.strip().casefold(), printer.model.strip())
    if not reported:
        return ModelChoice(no_models=True)
    with new_session() as db:
        created = {name.casefold() for name in db.scalars(select(PrinterModel.name))}
    return ModelChoice(
        sorted((name for key, name in reported.items() if key not in created), key=str.casefold)
    )
