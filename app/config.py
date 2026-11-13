"""Configuration from environment variables (see .env.example)."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_LANGUAGE = "de"
DEFAULT_TIMEZONE = "Europe/Berlin"
DEFAULT_DATA_DIR = Path("data")
DATABASE_FILE = "makerhub3d.db"
SUPPORTED_LANGUAGES = ("de", "en")

# Scheme + host (+ optional port) only: no wildcards, paths, spaces or other characters
# that could widen or break the Content-Security-Policy header.
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$")


class ConfigError(ValueError):
    """Raised when an environment variable has an invalid value."""


def _port_in_range(origin: str) -> bool:
    port = origin.rsplit(":", 1)[-1]
    return not port.isdigit() or int(port) <= 65535


def parse_embed_origins(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return ()
    origins = []
    for item in raw.split(","):
        origin = item.strip()
        if not _ORIGIN.fullmatch(origin) or not _port_in_range(origin):
            raise ConfigError(
                "MAKERHUB_EMBED_ORIGINS is invalid: expected a comma-separated list of "
                "origins like https://host.example.com (no wildcards, no paths)."
            )
        origins.append(origin)
    return tuple(origins)


def parse_insecure_cookies(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"", "false"}:
        return False
    if value == "true":
        return True
    raise ConfigError("MAKERHUB_INSECURE_COOKIES is invalid: expected true or false.")


def parse_timezone(raw: str) -> str:
    name = raw.strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # OSError: a region folder like "Europe" instead of a zone like "Europe/Berlin"
        raise ConfigError(
            "MAKERHUB_TIMEZONE is invalid: expected a time zone name like Europe/Berlin."
        ) from None
    return name


MIN_TWO_FACTOR_KEY_LENGTH = 32


def parse_two_factor_key(raw: str) -> str | None:
    """The key for the two-factor secrets (issue #30); None when not set."""
    key = raw.strip()
    if not key:
        return None
    usable = key.isascii() and all(char.isprintable() and not char.isspace() for char in key)
    if len(key) < MIN_TWO_FACTOR_KEY_LENGTH or not usable:
        # The message never contains the value itself.
        raise ConfigError(
            "MAKERHUB_2FA_KEY is invalid: expected at least 32 characters without spaces, "
            "e.g. the output of: openssl rand -hex 32"
        )
    return key


def parse_language(raw: str) -> str:
    language = raw.strip().lower()
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


@dataclass(frozen=True)
class Settings:
    language: str = DEFAULT_LANGUAGE
    embed_origins: tuple[str, ...] = ()
    data_dir: Path = DEFAULT_DATA_DIR
    # True only for home networks without HTTPS: the login cookie is then sent unencrypted.
    insecure_cookies: bool = False
    # Time zone for showing times; times are stored in UTC.
    timezone: str = DEFAULT_TIMEZONE
    # Key for the two-factor secrets; None = two-factor login cannot be set up.
    # Not part of repr(), so it never shows up in error messages or logs.
    two_factor_key: str | None = field(default=None, repr=False)

    @property
    def database_path(self) -> Path:
        return self.data_dir / DATABASE_FILE

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            language=parse_language(os.environ.get("MAKERHUB_LANGUAGE", "")),
            embed_origins=parse_embed_origins(os.environ.get("MAKERHUB_EMBED_ORIGINS", "")),
            data_dir=Path(os.environ.get("MAKERHUB_DATA_DIR", "").strip() or DEFAULT_DATA_DIR),
            insecure_cookies=parse_insecure_cookies(
                os.environ.get("MAKERHUB_INSECURE_COOKIES", "")
            ),
            timezone=parse_timezone(os.environ.get("MAKERHUB_TIMEZONE", "")),
            two_factor_key=parse_two_factor_key(os.environ.get("MAKERHUB_2FA_KEY", "")),
        )
