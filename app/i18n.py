"""Translations: flat JSON files in app/locales, one per language."""

import json
from functools import lru_cache
from pathlib import Path

from app.config import DEFAULT_LANGUAGE

LOCALES_DIR = Path(__file__).parent / "locales"


@lru_cache
def load_locale(language: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{language}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def translate(language: str, key: str) -> str:
    # Fall back to German, then to the key itself, so a missing text never breaks a page.
    return load_locale(language).get(key) or load_locale(DEFAULT_LANGUAGE).get(key) or key
