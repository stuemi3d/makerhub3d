"""The optional image of an article (issue #267).

The type is told from the first bytes of the file, never from its name or from what the browser
says: only JPG, PNG and WebP are accepted (no SVG, which can hold scripts). The image is stored
unchanged under a generated name in its own folder; it is never opened or converted on the
server. The name of the upload is only kept as text (cleaned) for showing it.

Files are removed from the disk only after the database change is committed, and a newly stored
file is removed again when the change is rolled back (see `queue_delete` and `track_new`, the
listeners are in `app.main`).
"""

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.calculation_file_store import FileStore, valid_stored, valid_token
from app.calculation_files import clean_name
from app.models import Article

FOLDER = "article_images"
MAX_IMAGE = 5 * 1024 * 1024
# Ending of the stored file -> type the browser is told
MEDIA_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
SUFFIXES = tuple(MEDIA_TYPES)
DELETE_KEY = "article_images_to_delete"
NEW_KEY = "article_images_new"
_HEAD = 12


def valid_name(text: str | None) -> bool:
    return valid_stored(text, SUFFIXES)


def image_store(data_dir: Path) -> FileStore:
    return FileStore(data_dir, FOLDER, SUFFIXES)


def suffix_of(head: bytes) -> str | None:
    """The ending for the image type found in the first bytes, or None for anything else."""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(head) >= _HEAD and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def check(data: bytes) -> str | None:
    """The translation key of why the upload cannot be used, or None when it is an image."""
    if len(data) > MAX_IMAGE:
        return "article.error.image_too_large"
    if suffix_of(data[:_HEAD]) is None:
        return "article.error.image_type"
    return None


@dataclass(frozen=True)
class Chosen:
    """An image that was checked and waits in the store until the article is saved."""

    token: str
    name: str


@dataclass(frozen=True)
class ImageForm:
    """What the form says about the image: a new one waiting, removing the old one, or neither
    (the stored image stays). `error` is the translation key of a refused upload."""

    chosen: Chosen | None = None
    remove: bool = False
    error: str | None = None


def adopt(store: FileStore, token: str) -> str | None:
    """Moves a waiting image to its place; the type is checked again from the file itself."""
    if not store.has_pending(token):
        return None
    try:
        with open(store.pending / token, "rb") as handle:
            suffix = suffix_of(handle.read(_HEAD))
    except OSError:
        return None
    if suffix is None:
        return None
    return store.adopt(token, suffix)


def waiting(store: FileStore, token: str, name: str) -> Chosen | None:
    """The image a refused form kept waiting, or None when the token is not (or no longer)
    known."""
    if not valid_token(token) or not store.has_pending(token):
        return None
    return Chosen(token, clean_name(name) or "?")


def queue_delete(db: Session, name: str | None) -> None:
    """Remembers a stored image that is no longer used; it goes when the change is committed."""
    if name:
        db.info.setdefault(DELETE_KEY, []).append(name)


def track_new(db: Session, name: str) -> None:
    """Remembers a newly stored image; it goes again when the change is rolled back."""
    db.info.setdefault(NEW_KEY, []).append(name)


async def read(request: Request, store: FileStore) -> ImageForm:
    """Reads the image part of the article form. A new upload is checked and kept waiting at
    once, so that it survives a form that is refused for another reason."""
    form = await request.form()
    remove = form.get("image_remove") == "1"
    token, name = form.get("image_token"), form.get("image_token_name")
    chosen = None
    if isinstance(token, str) and token:
        chosen = await run_in_threadpool(
            waiting, store, token, name if isinstance(name, str) else ""
        )
    upload = form.get("image")
    if isinstance(upload, UploadFile) and upload.filename:
        data = await upload.read(MAX_IMAGE + 1)
        error = check(data)
        if error:
            # A refused new upload does not throw away an image that was already waiting
            return ImageForm(chosen=chosen, remove=remove, error=error)
        token = await run_in_threadpool(store.keep, data)
        chosen = Chosen(token, clean_name(upload.filename) or "?")
    return ImageForm(chosen=chosen, remove=remove)


def apply(db: Session, store: FileStore, article: Article, image: ImageForm) -> dict | None:
    """Puts the chosen image on the article, or removes it; returns the change for the log.
    A new image wins over "remove". Call it only when the article will be saved."""
    old_name = article.image_name or ""
    if image.chosen is not None:
        stored = adopt(store, image.chosen.token)
        if stored is None:
            return None
        track_new(db, stored)
        queue_delete(db, article.image_stored)
        article.image_stored, article.image_name = stored, image.chosen.name
        return {"field": "image", "old": old_name, "new": image.chosen.name}
    if image.remove and article.image_stored:
        queue_delete(db, article.image_stored)
        article.image_stored = article.image_name = None
        return {"field": "image", "old": old_name, "new": ""}
    return None
