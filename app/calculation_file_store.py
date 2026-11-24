"""The .gcode.3mf files of calculations on disk (issue #182).

A checked upload first waits in a temporary place under a random token that travels in the form;
saving the calculation moves it to its final place under another generated name. The name of
the upload never becomes a path: every path here is built from a name that matches a fixed
pattern of hexadecimal characters. Nothing is unpacked here (the file is only read in memory by
`slice_reader`).

The same store keeps the images and .gcode.3mf files of articles (issues #267 and #268), each in
a folder of their own (see `app.article_attachments`).
"""

import contextlib
import os
import re
import secrets
import shutil
import time
from pathlib import Path

FOLDER = "calculation_files"
PENDING = "pending"
SUFFIX = ".gcode.3mf"
# An upload that is not saved within a day is forgotten
PENDING_SECONDS = 24 * 3600
_TOKEN = re.compile(r"[0-9a-f]{32}")


def valid_token(text: str) -> bool:
    return bool(_TOKEN.fullmatch(text))


def valid_stored(text: str | None, suffixes: tuple[str, ...]) -> bool:
    """True for a generated name: 32 hexadecimal characters and one of the given endings."""
    if not text or not _TOKEN.fullmatch(text[:32]):
        return False
    return text[32:] in suffixes


def valid_name(text: str | None) -> bool:
    return valid_stored(text, (SUFFIX,))


class FileStore:
    def __init__(
        self, data_dir: Path, folder: str = FOLDER, suffixes: tuple[str, ...] = (SUFFIX,)
    ) -> None:
        self.folder = data_dir / folder
        self.pending = self.folder / PENDING
        self.suffixes = suffixes

    def valid(self, name: str | None) -> bool:
        return valid_stored(name, self.suffixes)

    def _make_folders(self) -> None:
        self.pending.mkdir(parents=True, exist_ok=True, mode=0o700)

    def keep(self, data: bytes) -> str:
        """Keeps a checked upload until the calculation is saved; returns its token."""
        self._make_folders()
        self.clean_pending()
        token = secrets.token_hex(16)
        with open(self.pending / token, "xb") as handle:
            handle.write(data)
        os.chmod(self.pending / token, 0o600)
        return token

    def has_pending(self, token: str) -> bool:
        return valid_token(token) and (self.pending / token).is_file()

    def adopt(self, token: str, suffix: str | None = None) -> str | None:
        """Moves the waiting upload to its final place; returns the generated name, or None when
        the token is unknown (also when it was used before)."""
        suffix = self.suffixes[0] if suffix is None else suffix
        if not valid_token(token) or suffix not in self.suffixes:
            return None
        source = self.pending / token
        name = secrets.token_hex(16) + suffix
        try:
            os.replace(source, self.folder / name)
        except OSError:
            return None
        return name

    def copy_of(self, name: str | None) -> str | None:
        """A new waiting upload with the content of a stored file (for a copy of a calculation)."""
        path = self.path_of(name)
        if path is None:
            return None
        self._make_folders()
        self.clean_pending()
        token = secrets.token_hex(16)
        try:
            shutil.copyfile(path, self.pending / token)
        except OSError:
            return None
        os.chmod(self.pending / token, 0o600)
        return token

    def path_of(self, name: str | None) -> Path | None:
        """The path of a stored file, or None for a name that is not ours or a missing file."""
        if not self.valid(name):
            return None
        path = self.folder / str(name)
        return path if path.is_file() else None

    def delete(self, name: str | None) -> None:
        if self.valid(name):
            with contextlib.suppress(OSError):
                (self.folder / str(name)).unlink()

    def clean_pending(self, now: float | None = None) -> None:
        """Deletes the waiting uploads that nobody saved."""
        limit = (time.time() if now is None else now) - PENDING_SECONDS
        if not self.pending.is_dir():
            return
        for path in self.pending.iterdir():
            with contextlib.suppress(OSError):
                if path.is_file() and path.stat().st_mtime < limit:
                    path.unlink()
