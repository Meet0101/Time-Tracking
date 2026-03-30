from __future__ import annotations

from django.core.exceptions import PermissionDenied

from .models import Project, Task, User


def is_superadmin(user: User) -> bool:
    return bool(getattr(user, "is_superuser", False))


def is_team_lead(user: User) -> bool:
    return is_superadmin(user) or user.role in ("admin", "manager")


def require_team(user: User):
    # Users created before teams migration will be backfilled, but keep this defensive.
    if is_superadmin(user):
        return None
    if not getattr(user, "team_id", None):
        raise PermissionDenied("Team not assigned.")
    return user.team


def projects_qs_for(user: User):
    if is_superadmin(user):
        return Project.objects.all()
    require_team(user)
    return Project.objects.filter(team_id=user.team_id)


def tasks_qs_for(user: User):
    if is_superadmin(user):
        return Task.objects.select_related("module__project", "assigned_to")
    require_team(user)
    qs = Task.objects.select_related("module__project", "assigned_to").filter(module__project__team_id=user.team_id)
    if user.role == "developer":
        return qs.filter(assigned_to=user)
    return qs


def assert_can_manage_team(user: User):
    if not is_team_lead(user):
        raise PermissionDenied("You don't have permission.")

