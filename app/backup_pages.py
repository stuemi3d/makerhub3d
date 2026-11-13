"""Admin page for the database backups (issue #9): list, make one now, download."""

from collections.abc import Callable
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import audit, backup
from app.config import Settings
from app.permissions import Permission, requires

_TIME_FORMATS = {"de": "%d.%m.%Y %H:%M", "en": "%Y-%m-%d %H:%M"}


def format_size(size: int, language: str) -> str:
    if size < 1024 * 1024:
        text = f"{max(1, round(size / 1024))} kB"
    else:
        text = f"{size / (1024 * 1024):.1f} MB"
        if language == "de":
            text = text.replace(".", ",")
    return text


def add_backup_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    settings: Settings,
) -> None:
    manage = [requires(Permission.MANAGE)]
    zone = ZoneInfo(settings.timezone)
    time_format = _TIME_FORMATS.get(settings.language, _TIME_FORMATS["en"])

    def show(request: Request, status_code: int = 200, **context) -> HTMLResponse:
        files = [
            {
                "name": item.name,
                "kind": item.kind.value,
                "time": item.created_at.astimezone(zone).strftime(time_format),
                "size": format_size(item.size, settings.language),
            }
            for item in backup.list_backups(settings.backup_dir)
        ]
        context.setdefault("created", False)
        context.setdefault("failed", False)
        return render(
            request, "backups.html", "nav.settings", status_code=status_code, files=files, **context
        )

    @app.get("/settings/backups", response_class=HTMLResponse, dependencies=manage)
    def backups_page(request: Request, created: str | None = None) -> HTMLResponse:
        return show(request, created=created == "1")

    @app.post("/settings/backups", response_class=HTMLResponse, dependencies=manage)
    async def create_backup_now(request: Request) -> Response:
        try:
            path = await run_in_threadpool(backup.make_backup, settings, backup.Kind.MANUAL)
        except backup.BackupError:
            return show(request, status_code=500, failed=True)
        with new_session() as db, db.begin():
            audit.record_change(db, request.state.user.id, "backup.created", path.name)
        return RedirectResponse("/settings/backups?created=1", status_code=303)

    @app.get("/settings/backups/{name}/download", dependencies=manage)
    def download_backup(request: Request, name: str) -> Response:
        path = backup.find_backup(settings.backup_dir, name)
        if path is None:
            raise HTTPException(status_code=404)
        with new_session() as db, db.begin():
            audit.record_change(db, request.state.user.id, "backup.downloaded", name)
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=name,
            headers={"Cache-Control": "no-store"},
        )
