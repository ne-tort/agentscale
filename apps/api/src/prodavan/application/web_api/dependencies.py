"""Auth and scope contracts for the web API facade.

Adapters should use the existing ``PrincipalDep``/``SessionDep`` providers and
map their result into these contracts. Keeping this module free of FastAPI
wiring prevents a facade contract from changing existing routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, FrozenSet

from prodavan.application.web_facade.auth import WebRequestContext
from prodavan.application.web_facade.contracts import CurrentUser
from prodavan.domain.identity import Principal, WorkContext


class WebApiScope(StrEnum):
    CURRENT_USER_READ = "web:current_user:read"
    PROJECTS_READ = "web:projects:read"
    CONTAINER_STATUS_READ = "web:container_status:read"
    CHAT_SIDEBAR_READ = "web:chat_sidebar:read"
    CHAT_TRANSCRIPT_READ = "web:chat_transcript:read"
    CHAT_RUN = "web:chat_run"
    CHAT_STREAM = "web:chat_stream"


WEB_API_SCOPES: Final[FrozenSet[WebApiScope]] = frozenset(WebApiScope)


@dataclass(frozen=True, slots=True)
class WebApiAuthContext:
    """Request auth context required by every first-slice facade method.

    ``principal`` is validated by the existing Bearer/JWKS dependency;
    ``employee_id`` is the active employee entitlement used for cabinet and
    project authorization. ``cabinet_id``/``project_id`` come from route or
    request context and are never trusted from JWT claims.
    """

    principal: Principal
    employee_id: str
    scopes: frozenset[WebApiScope]
    company_id: str | None = None
    work_context: WorkContext = WorkContext()

    def require(self, scope: WebApiScope) -> None:
        if scope not in self.scopes:
            raise PermissionError(f"missing web API scope: {scope.value}")

    def require_cabinet(self, cabinet_id: str) -> None:
        expected = self.work_context.cabinet_id
        if expected != cabinet_id:
            raise PermissionError("cabinet context does not match requested cabinet")

    def require_project(self, project_id: str) -> None:
        expected = self.work_context.project_id
        if expected != project_id:
            raise PermissionError("project context does not match requested project")

    def to_facade_context(self) -> WebRequestContext:
        return WebRequestContext(
            user=CurrentUser(
                id=self.principal.sub,
                email=self.principal.email,
                display_name=self.principal.username,
                roles=tuple(sorted(self.principal.roles)),
                is_platform_admin=self.principal.is_platform_admin,
            ),
            company_id=self.company_id,
            cabinet_id=self.work_context.cabinet_id,
            project_id=self.work_context.project_id,
        )
