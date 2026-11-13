"""Admin pages for the change log and the login log (issue #29). Read only."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.i18n import translate
from app.models import ChangeLogEntry, LoginLogEntry, User
from app.permissions import Permission, requires
from app.settings_form import DECIMAL_FIELDS, format_decimal

PAGE_SIZE = 50
_DECIMAL_NAMES = {field.name for field in DECIMAL_FIELDS} | {"selling_price", "weight"}
_ARTICLE_FIELDS = {"number", "name", "selling_price", "weight", "min_stock"}
_STOCK_FIELDS = {"quantity", "reason", "date", "cancels"}
_TIME_FORMATS = {"de": "%d.%m.%Y %H:%M", "en": "%Y-%m-%d %H:%M"}


def _label(field: str, language: str) -> str:
    if field in _ARTICLE_FIELDS:
        return translate(language, f"article.field.{field}")
    if field in _STOCK_FIELDS:
        return translate(language, f"stock.field.{field}")
    key = "user.field.role" if field == "role" else f"settings.field.{field}"
    return translate(language, key)


def _value(field: str, value: str, language: str) -> str:
    """A stored value as the pages show it (decimal comma, translated choices)."""
    if not value:
        return "-"
    if field in _DECIMAL_NAMES:
        return format_decimal(Decimal(value), language)
    if field == "rounding":
        return translate(language, f"settings.rounding.{value}")
    if field == "role":
        return translate(language, f"role.{value}")
    return value


def describe_details(details: str, language: str) -> list[str]:
    return [
        f"{_label(item['field'], language)}: "
        f"{_value(item['field'], item['old'], language)} → "
        f"{_value(item['field'], item['new'], language)}"
        for item in json.loads(details)
    ]


def add_log_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    language: str,
    timezone: str,
) -> None:
    zone = ZoneInfo(timezone)
    time_format = _TIME_FORMATS.get(language, _TIME_FORMATS["en"])
    manage = [requires(Permission.MANAGE)]

    def local_time(value: datetime) -> str:
        return value.replace(tzinfo=UTC).astimezone(zone).strftime(time_format)

    def page_of(db: Session, model, page: int):
        total = db.scalar(select(func.count(model.id)))
        last_page = max(1, -(-total // PAGE_SIZE))
        page = min(max(page, 1), last_page)
        rows = db.execute(
            select(model, User.username)
            .outerjoin(User, model.user_id == User.id)
            .order_by(model.id.desc())
            .limit(PAGE_SIZE)
            .offset((page - 1) * PAGE_SIZE)
        ).all()
        return rows, page, last_page

    @app.get("/settings/log", response_class=HTMLResponse, dependencies=manage)
    def change_log_page(request: Request, page: int = 1) -> HTMLResponse:
        with new_session() as db:
            rows, page, last_page = page_of(db, ChangeLogEntry, page)
            entries = [
                {
                    "time": local_time(entry.created_at),
                    "user": username,
                    "action": f"log.action.{entry.action}",
                    "subject": entry.subject,
                    "details": describe_details(entry.details, language),
                }
                for entry, username in rows
            ]
        return render(
            request,
            "log.html",
            "nav.settings",
            log_tab="changes",
            entries=entries,
            page=page,
            last_page=last_page,
            base_url="/settings/log",
        )

    @app.get("/settings/log/logins", response_class=HTMLResponse, dependencies=manage)
    def login_log_page(request: Request, page: int = 1) -> HTMLResponse:
        with new_session() as db:
            rows, page, last_page = page_of(db, LoginLogEntry, page)
            entries = [
                {
                    "time": local_time(entry.created_at),
                    "user": username,
                    "event": f"log.event.{entry.event}",
                }
                for entry, username in rows
            ]
        return render(
            request,
            "log.html",
            "nav.settings",
            log_tab="logins",
            entries=entries,
            page=page,
            last_page=last_page,
            base_url="/settings/log/logins",
        )
