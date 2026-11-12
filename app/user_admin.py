"""User management for admins (issue #28): list, create, change role and state, reset password.

Users are never deleted, only deactivated, so that the change log (#29) keeps its names.
"""

import threading
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, auth
from app.models import User, UserRole
from app.permissions import Permission, requires

ROLES = [role.value for role in UserRole]
NEW_USER_FIELDS = ("username", "role", "password", "password_repeat")
ACTIVE_CHOICES = {"1": True, "0": False}

# Changes to users run one after another, so that the check "at least one active admin
# remains" cannot be bypassed by two changes at the same moment (single process).
_users_lock = threading.Lock()


def _user_view(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "active": user.active,
        "locked_minutes": auth.minutes_locked(user),
        "must_change_password": user.must_change_password,
    }


def _other_active_admins(db: Session, user_id: int) -> int:
    return db.scalar(
        select(func.count(User.id)).where(
            User.role == UserRole.ADMIN, User.active.is_(True), User.id != user_id
        )
    )


def add_user_admin_routes(
    app: FastAPI,
    new_session: Callable[[], Session],
    render: Callable[..., HTMLResponse],
    read_form: Callable[[Request, tuple[str, ...]], Awaitable[dict[str, str]]],
) -> None:
    manage = [requires(Permission.MANAGE)]

    def load_user(user_id: int) -> dict:
        with new_session() as db:
            user = db.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404)
            return _user_view(user)

    def render_edit(
        request: Request, user: dict, status_code: int = 200, **context
    ) -> HTMLResponse:
        context.setdefault(
            "values", {"role": user["role"], "active": "1" if user["active"] else "0"}
        )
        context.setdefault("errors", {})
        return render(
            request,
            "user_edit.html",
            "nav.settings",
            status_code=status_code,
            target=user,
            is_self=user["id"] == request.state.user.id,
            roles=ROLES,
            **context,
        )

    @app.get("/settings/users", response_class=HTMLResponse, dependencies=manage)
    def users_page(request: Request) -> HTMLResponse:
        with new_session() as db:
            users = [
                _user_view(user) for user in db.scalars(select(User).order_by(User.username_key))
            ]
        return render(request, "users.html", "nav.settings", users=users)

    @app.get("/settings/users/new", response_class=HTMLResponse, dependencies=manage)
    def new_user_page(request: Request) -> HTMLResponse:
        values = {"role": UserRole.EMPLOYEE.value}
        return render(
            request, "user_new.html", "nav.settings", values=values, errors={}, roles=ROLES
        )

    def create(actor_id: int, raw: dict[str, str]) -> dict[str, str]:
        errors = {}
        if error := auth.username_error(raw.get("username", "")):
            errors["username"] = error
        if raw.get("role") not in ROLES:
            errors["role"] = "user.error.choice"
        errors.update(auth.password_errors(raw))
        with _users_lock, new_session() as db, db.begin():
            if "username" not in errors and auth.name_taken(db, raw["username"]):
                errors["username"] = "user.error.taken"
            if errors:
                return errors
            # The admin knows this password, so the user has to change it first (#32).
            user = auth.create_user(
                db,
                raw["username"],
                raw["password"],
                UserRole(raw["role"]),
                must_change_password=True,
            )
            details = [{"field": "role", "old": "", "new": user.role}]
            audit.record_change(db, actor_id, "user.created", user.username, details)
        return {}

    @app.post("/settings/users/new", response_class=HTMLResponse, dependencies=manage)
    async def create_user(request: Request) -> Response:
        raw = await read_form(request, NEW_USER_FIELDS)
        errors = await run_in_threadpool(create, request.state.user.id, raw)
        if errors:
            # Passwords are never sent back.
            values = {"username": raw.get("username", ""), "role": raw.get("role", "")}
            return render(
                request,
                "user_new.html",
                "nav.settings",
                status_code=422,
                values=values,
                errors=errors,
                roles=ROLES,
            )
        return RedirectResponse("/settings/users", status_code=303)

    @app.get("/settings/users/{user_id}", response_class=HTMLResponse, dependencies=manage)
    def edit_user_page(
        request: Request, user_id: int, saved: str | None = None, password_reset: str | None = None
    ) -> HTMLResponse:
        return render_edit(
            request,
            load_user(user_id),
            saved=saved == "1",
            password_reset=password_reset == "1",  # noqa: S105 (a flag, not a password)
        )

    def change(user_id: int, actor_id: int, raw: dict[str, str]) -> tuple[dict, dict]:
        """Saves role and state; returns the errors (empty when saved) and the user."""
        with _users_lock, new_session() as db, db.begin():
            user = db.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404)
            errors = {}
            role = raw.get("role", "")
            active = ACTIVE_CHOICES.get(raw.get("active", ""))
            if role not in ROLES:
                errors["role"] = "user.error.choice"
            if active is None:
                errors["active"] = "user.error.choice"
            if not errors:
                removes_admin = role != UserRole.ADMIN or not active
                if user.id == actor_id and removes_admin:
                    errors["form"] = "user.error.self"
                elif (
                    user.role == UserRole.ADMIN
                    and user.active
                    and removes_admin
                    and _other_active_admins(db, user.id) == 0
                ):
                    errors["form"] = "user.error.last_admin"
            if errors:
                return errors, _user_view(user)
            if user.role != role:
                details = [{"field": "role", "old": str(user.role), "new": role}]
                audit.record_change(db, actor_id, "user.role_changed", user.username, details)
                user.role = UserRole(role)
            if user.active != active:
                action = "user.activated" if active else "user.deactivated"
                audit.record_change(db, actor_id, action, user.username)
                user.active = active
            if not active:
                auth.end_all_sessions(db, user.id)
            return {}, _user_view(user)

    @app.post("/settings/users/{user_id}", response_class=HTMLResponse, dependencies=manage)
    async def save_user(request: Request, user_id: int) -> Response:
        raw = await read_form(request, ("role", "active"))
        errors, user = await run_in_threadpool(change, user_id, request.state.user.id, raw)
        if errors:
            values = {"role": raw.get("role", ""), "active": raw.get("active", "")}
            return render_edit(request, user, status_code=422, values=values, errors=errors)
        # The address is built from the stored user, not from the requested one.
        return RedirectResponse(f"/settings/users/{int(user['id'])}?saved=1", status_code=303)

    def reset(user_id: int, actor_id: int, raw: dict[str, str]) -> tuple[dict, dict]:
        errors = auth.password_errors(raw)
        with _users_lock, new_session() as db, db.begin():
            user = db.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404)
            if user.id == actor_id:
                # The own password is changed under "Mein Konto" (issue #32), with the old one.
                errors = {"form": "user.error.self_password"}
            if not errors:
                auth.reset_password(db, user, raw["password"])
                audit.record_change(db, actor_id, "user.password_reset", user.username)
            return errors, _user_view(user)

    @app.post(
        "/settings/users/{user_id}/password", response_class=HTMLResponse, dependencies=manage
    )
    async def reset_password(request: Request, user_id: int) -> Response:
        raw = await read_form(request, ("password", "password_repeat"))
        errors, user = await run_in_threadpool(reset, user_id, request.state.user.id, raw)
        if errors:
            return render_edit(request, user, status_code=422, errors=errors)
        target = f"/settings/users/{int(user['id'])}?password_reset=1"
        return RedirectResponse(target, status_code=303)
