"""User management for admins (issue #28): list, create, change role and state, reset password.

Issue #39: the admin can also reset the two-factor login of another user (lost phone).

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
from app.article_pages import saved_in_popup, wants_popup
from app.models import User, UserRole
from app.permissions import Permission, requires
from app.table_sort import chosen_sort, sort_links

ROLES = [role.value for role in UserRole]
NEW_USER_FIELDS = ("username", "role", "password", "password_repeat")
ACTIVE_CHOICES = {"1": True, "0": False}
USER_SORT_KEYS = ("name", "role", "status")

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
        "two_factor": user.totp_secret is not None,
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
    def users_page(
        request: Request, sort: str | None = None, dir: str | None = None
    ) -> HTMLResponse:
        chosen = chosen_sort(USER_SORT_KEYS, sort, dir)
        with new_session() as db:
            users = [
                _user_view(user) for user in db.scalars(select(User).order_by(User.username_key))
            ]
        if chosen:
            key, direction = chosen
            column = {
                "name": lambda u: u["username"].casefold(),
                "role": lambda u: u["role"],
                "status": lambda u: (not u["active"], bool(u["locked_minutes"])),
            }[key]
            users.sort(key=column, reverse=direction == "desc")
        return render(
            request,
            "users.html",
            "nav.settings",
            users=users,
            sort_links=sort_links("/settings/users", USER_SORT_KEYS, chosen, ("name", "asc")),
        )

    @app.get("/settings/users/new", response_class=HTMLResponse, dependencies=manage)
    def new_user_page(request: Request) -> HTMLResponse:
        return new_user_form(request, {"role": UserRole.EMPLOYEE.value}, {})

    def new_user_form(
        request: Request, values: dict, errors: dict, status_code: int = 200
    ) -> HTMLResponse:
        """The form: the whole page, or only the form for the pop-up (same template)."""
        response = render(
            request,
            "user_new.html",
            "nav.settings",
            status_code=status_code,
            values=values,
            errors=errors,
            roles=ROLES,
            popup=wants_popup(request),
        )
        response.headers["Vary"] = "HX-Request"
        return response

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
            return new_user_form(request, values, errors, 422)
        if wants_popup(request):
            return saved_in_popup()
        return RedirectResponse("/settings/users", status_code=303)

    @app.get("/settings/users/{user_id}", response_class=HTMLResponse, dependencies=manage)
    def edit_user_page(
        request: Request,
        user_id: int,
        saved: str | None = None,
        password_reset: str | None = None,
        tfa_reset: str | None = None,
    ) -> HTMLResponse:
        return render_edit(
            request,
            load_user(user_id),
            saved=saved == "1",
            password_reset=password_reset == "1",  # noqa: S105 (a flag, not a password)
            tfa_reset=tfa_reset == "1",
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

    @app.post(
        "/settings/users/{user_id}/2fa-reset", response_class=HTMLResponse, dependencies=manage
    )
    async def reset_two_factor(request: Request, user_id: int) -> Response:
        # The form has no fields; its form token (CSRF) is checked for every form in main.py.
        actor_id = request.state.user.id
        error, stored_id = await run_in_threadpool(
            auth.admin_reset_two_factor, new_session, actor_id, user_id
        )
        if stored_id is None:
            raise HTTPException(status_code=404)
        if error:
            return render_edit(
                request, load_user(stored_id), status_code=422, errors={"form": error}
            )
        # The address is built from the stored user, not from the requested one.
        return RedirectResponse(f"/settings/users/{int(stored_id)}?tfa_reset=1", status_code=303)
