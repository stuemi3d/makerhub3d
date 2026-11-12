"""Configuration from environment variables (see .env.example)."""

import os
import re
from dataclasses import dataclass

DEFAULT_LANGUAGE = "de"
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


def parse_language(raw: str) -> str:
    language = raw.strip().lower()
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


@dataclass(frozen=True)
class Settings:
    language: str = DEFAULT_LANGUAGE
    embed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            language=parse_language(os.environ.get("MAKERHUB_LANGUAGE", "")),
            embed_origins=parse_embed_origins(os.environ.get("MAKERHUB_EMBED_ORIGINS", "")),
        )
