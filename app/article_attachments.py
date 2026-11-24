"""The optional files of an article: an image (issue #267) and a .gcode.3mf file (issue #268).

Both work the same way; a `Kind` describes what differs:
- the image: the type is told from the first bytes of the file, never from its name or from
  what the browser says. Only JPG, PNG and WebP are accepted (no SVG, which can hold scripts).
  It is stored unchanged and never opened or converted on the server.
- the .gcode.3mf file: checked like the upload of a calculation (`calculation_files`,
  read in memory only, never unpacked on disk). It is only kept for downloading; nothing is
  calculated from it at the article.

A file is stored under a generated name in the kind's own folder below the data directory. The
name of the upload is only kept as text (cleaned) for showing it and for the download.

Files are removed from the disk only after the database change is committed, and a newly stored
file is removed again when the change is rolled back (see `queue_delete` and `track_new`; the
listeners are in `app.main`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import calculation_files
from app.calculation_file_store import FileStore, valid_stored, valid_token
from app.calculation_files import clean_name
from app.models import Article

MAX_IMAGE = 5 * 1024 * 1024
# Ending of a stored image -> type the browser is told
MEDIA_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
PRINT_SUFFIX = calculation_files.SUFFIX
DELETE_KEY = "article_files_to_delete"
NEW_KEY = "article_files_new"
_HEAD = 12


def image_suffix(head: bytes) -> str | None:
    """The ending for the image type found in the first bytes, or None for anything else."""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(head) >= _HEAD and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def check_image(data: bytes, name: str) -> str | None:
    """The translation key of why the upload cannot be used, or None when it is an image."""
    if len(data) > MAX_IMAGE:
        return "article.error.image_too_large"
    if image_suffix(data[:_HEAD]) is None:
        return "article.error.image_type"
    return None


def print_suffix(head: bytes) -> str | None:
    """A .gcode.3mf file is a zip archive; the full check was made at the upload."""
    return PRINT_SUFFIX if head.startswith(b"PK\x03\x04") else None


def check_print_file(data: bytes, name: str) -> str | None:
    """The same check as for the file of a calculation (size, content, name)."""
    return calculation_files.read_upload(data, name)[1]


@dataclass(frozen=True)
class Kind:
    """One kind of file of an article. `field` names the form field, the change-log field and
    the article's columns (`<field>_stored`, `<field>_name`)."""

    field: str
    folder: str
    suffixes: tuple[str, ...]
    max_bytes: Callable[[], int]
    suffix_of: Callable[[bytes], str | None]
    check: Callable[[bytes, str], str | None]


IMAGE = Kind(
    "image", "article_images", tuple(MEDIA_TYPES), lambda: MAX_IMAGE, image_suffix, check_image
)
PRINT_FILE = Kind(
    "print_file",
    "article_files",
    (PRINT_SUFFIX,),
    lambda: calculation_files.MAX_UPLOAD,
    print_suffix,
    check_print_file,
)
KINDS = (IMAGE, PRINT_FILE)
BY_FIELD = {kind.field: kind for kind in KINDS}


def body_limit() -> int:
    """What an article form with all its files may weigh."""
    return sum(kind.max_bytes() for kind in KINDS) + calculation_files.FORM_SLACK


def valid_name(kind: Kind, text: str | None) -> bool:
    return valid_stored(text, kind.suffixes)


def make_stores(data_dir: Path) -> dict[str, FileStore]:
    """One store per kind, keyed by its field."""
    return {kind.field: FileStore(data_dir, kind.folder, kind.suffixes) for kind in KINDS}


@dataclass(frozen=True)
class Chosen:
    """A file that was checked and waits in the store until the article is saved."""

    token: str
    name: str


@dataclass(frozen=True)
class FileForm:
    """What the form says about one kind of file: a new one waiting, removing the old one, or
    neither (the stored file stays). `error` is the translation key of a refused upload."""

    chosen: Chosen | None = None
    remove: bool = False
    error: str | None = None


def stored_of(article: Article, kind: Kind) -> str | None:
    return getattr(article, f"{kind.field}_stored")


def name_of(article: Article, kind: Kind) -> str | None:
    return getattr(article, f"{kind.field}_name")


def adopt(kind: Kind, store: FileStore, token: str) -> str | None:
    """Moves a waiting file to its place; the type is checked again from the file itself."""
    if not store.has_pending(token):
        return None
    try:
        with open(store.pending / token, "rb") as handle:
            suffix = kind.suffix_of(handle.read(_HEAD))
    except OSError:
        return None
    if suffix is None:
        return None
    return store.adopt(token, suffix)


def waiting(store: FileStore, token: str, name: str) -> Chosen | None:
    """The file a refused form kept waiting, or None when the token is not (or no longer)
    known."""
    if not valid_token(token) or not store.has_pending(token):
        return None
    return Chosen(token, clean_name(name) or "?")


def queue_delete(db: Session, kind: Kind, name: str | None) -> None:
    """Remembers a stored file that is no longer used; it goes when the change is committed."""
    if name:
        db.info.setdefault(DELETE_KEY, []).append((kind.field, name))


def queue_delete_all(db: Session, article: Article) -> None:
    """Remembers every file of an article that is deleted."""
    for kind in KINDS:
        queue_delete(db, kind, stored_of(article, kind))


def track_new(db: Session, kind: Kind, name: str) -> None:
    """Remembers a newly stored file; it goes again when the change is rolled back."""
    db.info.setdefault(NEW_KEY, []).append((kind.field, name))


async def read_one(request: Request, kind: Kind, store: FileStore) -> FileForm:
    form = await request.form()
    field = kind.field
    remove = form.get(f"{field}_remove") == "1"
    token, name = form.get(f"{field}_token"), form.get(f"{field}_token_name")
    chosen = None
    if isinstance(token, str) and token:
        chosen = await run_in_threadpool(
            waiting, store, token, name if isinstance(name, str) else ""
        )
    upload = form.get(field)
    if isinstance(upload, UploadFile) and upload.filename:
        shown = clean_name(upload.filename)
        data = await upload.read(kind.max_bytes() + 1)
        error = await run_in_threadpool(kind.check, data, shown)
        if error:
            # A refused new upload does not throw away a file that was already waiting
            return FileForm(chosen=chosen, remove=remove, error=error)
        token = await run_in_threadpool(store.keep, data)
        chosen = Chosen(token, shown or "?")
    return FileForm(chosen=chosen, remove=remove)


async def read(request: Request, stores: dict[str, FileStore]) -> dict[str, FileForm]:
    """Reads the file fields of the article form. A new upload is checked and kept waiting at
    once, so that it survives a form that is refused for another reason."""
    return {kind.field: await read_one(request, kind, stores[kind.field]) for kind in KINDS}


def errors_of(forms: dict[str, FileForm]) -> dict[str, str]:
    return {field: form.error for field, form in forms.items() if form.error}


def apply_one(
    db: Session, kind: Kind, store: FileStore, article: Article, form: FileForm
) -> dict | None:
    old_name = name_of(article, kind) or ""
    old_stored = stored_of(article, kind)
    if form.chosen is not None:
        stored = adopt(kind, store, form.chosen.token)
        if stored is None:
            return None
        track_new(db, kind, stored)
        queue_delete(db, kind, old_stored)
        setattr(article, f"{kind.field}_stored", stored)
        setattr(article, f"{kind.field}_name", form.chosen.name)
        return {"field": kind.field, "old": old_name, "new": form.chosen.name}
    if form.remove and old_stored:
        queue_delete(db, kind, old_stored)
        setattr(article, f"{kind.field}_stored", None)
        setattr(article, f"{kind.field}_name", None)
        return {"field": kind.field, "old": old_name, "new": ""}
    return None


def apply(
    db: Session, stores: dict[str, FileStore], article: Article, forms: dict[str, FileForm]
) -> list[dict]:
    """Puts the chosen files on the article, or removes them; returns the changes for the log.
    A new file wins over "remove". Call it only when the article will be saved."""
    changes = []
    for kind in KINDS:
        form = forms.get(kind.field)
        if form is not None:
            change = apply_one(db, kind, stores[kind.field], article, form)
            if change:
                changes.append(change)
    return changes


def context(forms: dict[str, FileForm], errors: dict[str, str], stores: dict[str, FileStore]):
    """What the form shows about the files after a refused save: the waiting files (their tokens
    travel in the form), the remove marks and the errors of refused uploads. A file that is no
    longer waiting (a save that failed at the last moment already took it) is not offered
    again."""
    errors.update(errors_of(forms))
    waiting_files = {}
    for field, form in forms.items():
        if form.chosen is not None and stores[field].has_pending(form.chosen.token):
            waiting_files[field] = form.chosen
    return {
        "waiting_files": waiting_files,
        "removing": {field: form.remove for field, form in forms.items()},
    }
