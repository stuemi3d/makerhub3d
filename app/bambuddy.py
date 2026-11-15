"""The one and only place that talks to Bambuddy (plan, section 2).

Read-only: this module sends GET requests and nothing else, so MakerHub 3D can never change
anything in Bambuddy. Errors carry a short code and never the address, the key or the
answer of the server, so they are safe to show.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx2

TIMEOUT_SECONDS = 10.0
DEFAULT_ARCHIVE_LIMIT = 500

# Scheme + host (+ port), optionally one trailing slash; no user name, path, query or spaces.
_ADDRESS = re.compile(
    r"https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(?::(?P<port>[0-9]{1,5}))?/?"
)

PRINTERS_PATH = "/api/v1/printers/"
ARCHIVES_PATH = "/api/v1/archives/"
SPOOLS_PATH = "/api/v1/inventory/spools"
SETTINGS_PATH = "/api/v1/settings/"


class BambuddyError(Exception):
    """Codes: unreachable, timeout, rejected, forbidden, server_error, bad_response."""

    def __init__(self, code: str, path: str = "") -> None:
        super().__init__(code)
        self.code = code
        # The Bambuddy path that failed (not secret); shown so the missing right is clear.
        self.path = path

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True)
class Archive:
    id: int
    name: str
    filename: str
    status: str
    printer_id: int | None
    started_at: datetime | None
    completed_at: datetime | None
    # The real duration if known, else the sliced estimate
    print_seconds: int | None
    filament_grams: Decimal | None
    energy_kwh: Decimal | None
    energy_cost: Decimal | None
    wear_cost: Decimal | None
    material_cost: Decimal | None
    failure_reason: str | None


@dataclass(frozen=True)
class Spool:
    id: int
    material: str
    color_name: str | None
    brand: str | None
    cost_per_kg: Decimal | None
    label_weight: Decimal | None
    weight_used: Decimal | None
    category: str | None


@dataclass(frozen=True)
class Printer:
    id: int
    name: str
    model: str | None


@dataclass(frozen=True)
class ElectricityPrice:
    per_kwh: Decimal
    currency: str


def valid_address(url: str) -> bool:
    match = _ADDRESS.fullmatch(url.strip())
    return match is not None and int(match["port"] or 0) <= 65535


def _bad() -> BambuddyError:
    return BambuddyError("bad_response")


def _text(item: dict, name: str, required: bool = False) -> str | None:
    value = item.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise _bad()
    return value


def _int(item: dict, name: str, required: bool = False) -> int | None:
    value = item.get(name)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad()
    return value


def _decimal(item: dict, name: str, required: bool = False) -> Decimal | None:
    value = item.get(name)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise _bad()
    return Decimal(value)


def _time(item: dict, name: str) -> datetime | None:
    value = _text(item, name)
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise _bad() from None


def _entry(item: Any) -> dict:
    if not isinstance(item, dict):
        raise _bad()
    return item


def _archive(raw: Any) -> Archive:
    item = _entry(raw)
    actual = _int(item, "actual_time_seconds")
    estimate = _int(item, "print_time_seconds")
    return Archive(
        id=_int(item, "id", required=True),
        name=_text(item, "print_name") or _text(item, "filename", required=True),
        filename=_text(item, "filename") or "",
        status=_text(item, "status", required=True),
        printer_id=_int(item, "printer_id"),
        started_at=_time(item, "started_at"),
        completed_at=_time(item, "completed_at"),
        print_seconds=actual if actual is not None else estimate,
        filament_grams=_decimal(item, "filament_used_grams"),
        energy_kwh=_decimal(item, "energy_kwh"),
        energy_cost=_decimal(item, "energy_cost"),
        wear_cost=_decimal(item, "wear_cost"),
        material_cost=_decimal(item, "cost"),
        failure_reason=_text(item, "failure_reason"),
    )


def _spool(raw: Any) -> Spool:
    item = _entry(raw)
    return Spool(
        id=_int(item, "id", required=True),
        material=_text(item, "material", required=True),
        color_name=_text(item, "color_name"),
        brand=_text(item, "brand"),
        cost_per_kg=_decimal(item, "cost_per_kg"),
        label_weight=_decimal(item, "label_weight"),
        weight_used=_decimal(item, "weight_used"),
        category=_text(item, "category"),
    )


def _printer(raw: Any) -> Printer:
    item = _entry(raw)
    return Printer(
        id=_int(item, "id", required=True),
        name=_text(item, "name", required=True),
        model=_text(item, "model"),
    )


class BambuddyClient:
    def __init__(
        self, base_url: str, api_key: str, transport: httpx2.BaseTransport | None = None
    ) -> None:
        if not valid_address(base_url):
            raise ValueError("invalid Bambuddy address")
        self._base_url = base_url.strip().rstrip("/")
        self._api_key = api_key
        self._transport = transport

    def _get(self, path: str, params: dict[str, int] | None = None) -> Any:
        try:
            # No redirects: they could carry the key to another host.
            with httpx2.Client(
                transport=self._transport, timeout=TIMEOUT_SECONDS, follow_redirects=False
            ) as http:
                response = http.get(
                    self._base_url + path, params=params, headers={"X-API-Key": self._api_key}
                )
        except httpx2.TimeoutException:
            raise BambuddyError("timeout", path) from None
        except httpx2.HTTPError:
            raise BambuddyError("unreachable", path) from None
        if response.status_code == 401:
            raise BambuddyError("rejected", path)
        if response.status_code == 403:
            raise BambuddyError("forbidden", path)
        if response.status_code >= 500:
            raise BambuddyError("server_error", path)
        if response.status_code != 200:
            raise BambuddyError("bad_response", path)
        try:
            # Numbers stay exact: money and weights are never floats.
            return json.loads(response.text, parse_float=Decimal)
        except (ValueError, InvalidOperation):
            raise BambuddyError("bad_response", path) from None

    def _list(self, path: str, params: dict[str, int] | None = None) -> list:
        data = self._get(path, params)
        if not isinstance(data, list):
            raise BambuddyError("bad_response", path)
        return data

    def archives(self, limit: int = DEFAULT_ARCHIVE_LIMIT, offset: int = 0) -> list[Archive]:
        return [_archive(i) for i in self._list(ARCHIVES_PATH, {"limit": limit, "offset": offset})]

    def archive(self, archive_id: int) -> Archive:
        return _archive(self._get(f"{ARCHIVES_PATH}{int(archive_id)}"))

    def spools(self) -> list[Spool]:
        return [_spool(i) for i in self._list(SPOOLS_PATH)]

    def printers(self) -> list[Printer]:
        return [_printer(i) for i in self._list(PRINTERS_PATH)]

    def electricity_price(self) -> ElectricityPrice:
        # Bambuddy's settings also hold tokens and passwords; only the price is taken.
        item = _entry(self._get(SETTINGS_PATH))
        return ElectricityPrice(
            per_kwh=_decimal(item, "energy_cost_per_kwh", required=True),
            currency=_text(item, "currency") or "EUR",
        )

    def check(self) -> None:
        """Reads everything MakerHub 3D needs once; raises the first BambuddyError."""
        self.printers()
        self.archives(limit=1)
        self.spools()
        self.electricity_price()
