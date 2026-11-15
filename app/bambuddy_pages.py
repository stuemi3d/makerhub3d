"""Settings tab for the Bambuddy connection (issue #117): address, key, connection test."""

from collections.abc import Callable

import httpx2
from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import audit
from app.bambuddy import BambuddyClient, BambuddyError, valid_address
from app.models import BambuddyConnection
from app.permissions import Permission, requires
from app.two_factor import Cipher

URL_MAX_LENGTH = 200
KEY_MAX_LENGTH = 200
KEY_CHANGED = "(neu gesetzt)"
KEY_REMOVED = "(entfernt)"


def missing_parts(url: str, has_key: bool) -> str | None:
    """Translation key of what is missing for the status, or None if it is set up."""
    if not url and not has_key:
        return "bambuddy.status.missing_both"
    if not url:
        return "bambuddy.status.missing_url"
    if not has_key:
        return "bambuddy.status.missing_key"
    return None


def connection_is_set_up(db: Session) -> bool:
    row = db.get(BambuddyConnection, 1)
    return row is not None and missing_parts(row.url, row.api_key_encrypted is not None) is None


def add_bambuddy_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    cipher: Cipher | None,
    render: Callable[..., HTMLResponse],
    read_form: Callable,
) -> None:
    manage = [requires(Permission.MANAGE)]

    def show(request: Request, status_code: int = 200, **context) -> HTMLResponse:
        with new_session() as db:
            row = db.get(BambuddyConnection, 1)
            url, has_key = row.url, row.api_key_encrypted is not None
        context.setdefault("values", {"url": url})
        context.setdefault("errors", {})
        context.setdefault("saved", False)
        context.setdefault("test_error", None)
        context.setdefault("test_ok", False)
        return render(
            request,
            "bambuddy.html",
            "nav.settings",
            status_code=status_code,
            has_key=has_key,
            missing=missing_parts(url, has_key),
            address=url,
            can_store_key=cipher is not None,
            **context,
        )

    @app.get("/settings/bambuddy", response_class=HTMLResponse, dependencies=manage)
    def bambuddy_page(request: Request, saved: str | None = None) -> HTMLResponse:
        return show(request, saved=saved == "1")

    @app.post("/settings/bambuddy", response_class=HTMLResponse, dependencies=manage)
    async def save_bambuddy(request: Request) -> Response:
        raw = await read_form(request, ("url", "api_key"))
        url = raw.get("url", "").strip()
        api_key = raw.get("api_key", "").strip()
        errors: dict[str, str] = {}
        if len(url) > URL_MAX_LENGTH or not valid_address(url):
            errors["url"] = "bambuddy.error.url"
        if len(api_key) > KEY_MAX_LENGTH:
            errors["api_key"] = "settings.error.too_long"
        elif api_key and cipher is None:
            errors["api_key"] = "bambuddy.error.no_cipher"
        if errors:
            # The key is never sent back to the browser.
            return show(request, status_code=422, values={"url": url}, errors=errors)
        url = url.rstrip("/")
        with new_session() as db, db.begin():
            row = db.get(BambuddyConnection, 1)
            changes = audit.field_changes({"url": row.url}, {"url": url})
            if url != row.url and not api_key and row.api_key_encrypted is not None:
                # A new address must not receive the old key.
                row.api_key_encrypted = None
                changes.append({"field": "api_key", "old": "", "new": KEY_REMOVED})
            row.url = url
            if api_key:
                row.api_key_encrypted = cipher.encrypt(api_key)
                # Only the fact is logged, never the key.
                changes.append({"field": "api_key", "old": "", "new": KEY_CHANGED})
            if changes:
                audit.record_change(db, request.state.user.id, "bambuddy.changed", "", changes)
        return RedirectResponse("/settings/bambuddy?saved=1", status_code=303)

    def run_test() -> str | tuple[str, str] | None:
        """None if all works, else the translation key (and the Bambuddy path) of the problem."""
        with new_session() as db:
            row = db.get(BambuddyConnection, 1)
            url, token = row.url, row.api_key_encrypted
        if not url or not token or cipher is None:
            return "bambuddy.error.not_configured"
        api_key = cipher.decrypt(token)
        if api_key is None:
            return "bambuddy.error.key_unreadable"
        transport: httpx2.BaseTransport | None = getattr(app.state, "bambuddy_transport", None)
        try:
            BambuddyClient(url, api_key, transport=transport).check()
        except BambuddyError as error:
            return f"bambuddy.error.{error.code}", error.path
        return None

    @app.post("/settings/bambuddy/test", response_class=HTMLResponse, dependencies=manage)
    async def test_bambuddy(request: Request) -> Response:
        problem = await run_in_threadpool(run_test)
        if problem is None:
            return show(request, test_ok=True)
        if isinstance(problem, str):
            problem = (problem, "")
        return show(request, test_error=problem)
