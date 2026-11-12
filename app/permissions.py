"""Rights of the fixed roles (plan, section 8) and the permission level of every page."""

from enum import StrEnum

from fastapi import Depends, Request
from fastapi.routing import APIRoute

from app.models import UserRole


class Permission(StrEnum):
    VIEW = "view"  # dashboard, articles, analysis
    EDIT = "edit"  # articles, calculation, production, stock, sales, cancelling
    MANAGE = "manage"  # settings, devices, Bambuddy connection, users, backup, change log


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    UserRole.ADMIN: frozenset({Permission.VIEW, Permission.EDIT, Permission.MANAGE}),
    UserRole.EMPLOYEE: frozenset({Permission.VIEW, Permission.EDIT}),
    UserRole.READ_ONLY: frozenset({Permission.VIEW}),
}


class PermissionDenied(Exception):
    """The logged-in user's role does not allow this page; answered with a 403 page."""


class _RequirePermission:
    def __init__(self, permission: Permission) -> None:
        self.permission = permission

    def __call__(self, request: Request) -> None:
        user = getattr(request.state, "user", None)
        if user is None or not has_permission(user.role, self.permission):
            raise PermissionDenied


def has_permission(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def requires(permission: Permission):
    """Dependency for a route: `@app.get(..., dependencies=[requires(Permission.VIEW)])`.

    Every page behind the login must have exactly one; a test checks this for all routes.
    """
    return Depends(_RequirePermission(permission))


def route_permission(route: APIRoute) -> Permission | None:
    """The permission level of a route, or None if it has none (or several)."""
    levels = [
        dependency.call.permission
        for dependency in route.dependant.dependencies
        if isinstance(dependency.call, _RequirePermission)
    ]
    return levels[0] if len(levels) == 1 else None
