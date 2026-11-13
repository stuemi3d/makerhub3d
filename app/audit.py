"""Change log and login log (issue #29). Entries are only ever added, never changed.

Passwords and password hashes never go into a log; callers only pass field names and
stored values of non-secret fields.
"""

import json
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import auth
from app.models import ChangeLogEntry, LoginLogEntry

LOGIN_LOG_RETENTION = timedelta(days=365)


def record_change(
    db: Session,
    user_id: int | None,
    action: str,
    subject: str = "",
    details: list[dict[str, str]] | None = None,
) -> None:
    db.add(
        ChangeLogEntry(
            created_at=auth.utcnow(),
            user_id=user_id,
            action=action,
            subject=subject,
            details=json.dumps(details or [], ensure_ascii=False),
        )
    )


def field_changes(old: dict[str, object], new: dict[str, object]) -> list[dict[str, str]]:
    """The fields whose value differs, in the order of `new`, as stored text."""
    return [
        {"field": name, "old": str(old[name]), "new": str(value)}
        for name, value in new.items()
        if old[name] != value
    ]


def record_login(db: Session, user_id: int | None, event: str) -> None:
    now = auth.utcnow()
    db.add(LoginLogEntry(created_at=now, user_id=user_id, event=event))
    # Old entries go away here, so the login log cannot grow without end.
    db.execute(delete(LoginLogEntry).where(LoginLogEntry.created_at < now - LOGIN_LOG_RETENTION))
