"""Authentication and tenant/scope boundary for the web facade."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import CurrentUser


class ScopeViolation(PermissionError):
    """Raised when a request crosses its authenticated tenant/resource scope."""


class ScopeKind(StrEnum):
    COMPANY = "company"
    CABINET = "cabinet"
    PROJECT = "project"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class WebRequestContext:
    """Authenticated identity plus the resource scope selected by the request."""

    user: CurrentUser
    company_id: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None

    def require_company(self, company_id: str) -> None:
        if self.user.is_platform_admin:
            return
        if not self.company_id or self.company_id != company_id:
            raise ScopeViolation("company is outside the authenticated scope")
        if self.user.company_id != company_id:
            raise ScopeViolation("company does not belong to the authenticated user")

    def require_cabinet(self, cabinet_id: str) -> None:
        if self.user.is_platform_admin and self.cabinet_id in (None, cabinet_id):
            return
        if self.cabinet_id != cabinet_id:
            raise ScopeViolation("cabinet is outside the authenticated scope")

    def require_project(self, project_id: str) -> None:
        if self.user.is_platform_admin and self.project_id in (None, project_id):
            return
        if self.project_id != project_id:
            raise ScopeViolation("project is outside the authenticated scope")

    def require_session(self, session_id: str) -> None:
        if self.session_id != session_id:
            raise ScopeViolation("session is outside the authenticated scope")


class ScopeBoundary:
    """Small deny-by-default guard reusable by every facade application port."""

    def __init__(self, context: WebRequestContext) -> None:
        self.context = context

    def project(self, project_id: str) -> WebRequestContext:
        self.context.require_project(project_id)
        return self.context

    def session(self, project_id: str, session_id: str) -> WebRequestContext:
        self.context.require_project(project_id)
        self.context.require_session(session_id)
        return self.context
