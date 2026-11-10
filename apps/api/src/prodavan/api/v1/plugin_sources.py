"""Company/plugin-scoped source provisioning and status API.

The composition root installs a request-session service factory. Missing
composition fails closed with 503; no provider credential or organization is
accepted from the request. Responses intentionally omit the stored secret REF.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.api.exception_handlers import (
    app_error_handler,
    http_exception_handler,
    validation_exception_handler,
)
from prodavan.application.identity.service import EntitlementService
from prodavan.application.project_service.access import ProjectAccessPolicy
from prodavan.application.plugins.sources.repository import SqlModuleSourceRepository
from prodavan.application.plugins.sources.service import (
    ModuleSourceProvisioningConfig,
    ModuleSourceService,
    ModuleSourceUnavailableError,
)
from prodavan.application.plugins.sources.types import ModuleSource, ModuleSourceStatus
from prodavan.domain.errors import AppError
from prodavan.domain.plugins.types import PLUGIN_ID_PATTERN
from prodavan.infrastructure.persistence.database import get_session_factory
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from prodavan.infrastructure.plugins.sources.ports import GitHubRepositorySourcePort


class _NoStoreRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except AppError as exc:
                response = await app_error_handler(request, exc)
            except RequestValidationError as exc:
                response = await validation_exception_handler(request, exc)
            except StarletteHTTPException as exc:
                response = await http_exception_handler(request, exc)
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


router = APIRouter(
    prefix="/companies/{company_id}/plugins/{plugin_id}/source",
    tags=["plugin-sources"],
    route_class=_NoStoreRoute,
)

SERVICE_FACTORY_STATE = "module_source_service_factory"
ServiceFactory = Callable[[AsyncSession], ModuleSourceService]
EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]
CompanyId = Annotated[str, Path(min_length=1, max_length=40)]
PluginId = Annotated[str, Path(min_length=1, max_length=160, pattern=PLUGIN_ID_PATTERN.pattern)]


class CreateModuleSourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]


class BindModuleSourceScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cabinet_id: str
    project_id: str


class ModuleSourceResponse(BaseModel):
    """Public source metadata/provenance; deliberately no credential REF."""

    model_config = ConfigDict(extra="forbid")

    id: str
    company_id: str
    plugin_id: str
    plugin_instance_id: str
    cabinet_id: str | None = None
    project_id: str | None = None
    provider: Literal["github_app"]
    repo_id: int
    full_name: str
    private: bool
    default_branch: str
    expected_ref: str
    trusted_workflow_ref: str
    expected_signer_identity: str
    status: ModuleSourceStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_source(cls, source: ModuleSource) -> ModuleSourceResponse:
        return cls(
            id=source.id,
            company_id=source.company_id,
            plugin_id=source.plugin_id,
            plugin_instance_id=source.plugin_instance_id,
            cabinet_id=source.cabinet_id,
            project_id=source.project_id,
            provider="github_app",
            repo_id=source.repo_id,
            full_name=source.full_name,
            private=source.private,
            default_branch=source.default_branch,
            expected_ref=source.expected_ref,
            trusted_workflow_ref=source.trusted_workflow_ref,
            expected_signer_identity=source.expected_signer_identity,
            status=source.status,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )


def make_module_source_service_factory(
    *,
    provider: GitHubRepositorySourcePort,
    config: ModuleSourceProvisioningConfig,
) -> ServiceFactory:
    """Build request entitlement and commit-owning source persistence."""

    def factory(session: AsyncSession) -> ModuleSourceService:
        return ModuleSourceService(
            provider=provider,
            repository=SqlModuleSourceRepository(
                session_factory=get_session_factory()
            ),
            entitlement=EntitlementService(session),
            config=config,
            project_access=ProjectAccessPolicy(session),
        )

    return factory


def install_module_source_service_factory(app: Any, factory: ServiceFactory) -> None:
    """Composition hook; the factory receives the request AsyncSession."""

    if not callable(factory):
        raise TypeError("module source service factory must be callable")
    setattr(app.state, SERVICE_FACTORY_STATE, factory)


def _service(request: Request, session: AsyncSession) -> ModuleSourceService:
    factory = getattr(request.app.state, SERVICE_FACTORY_STATE, None)
    if not callable(factory):
        raise ModuleSourceUnavailableError(
            code="MODULE_SOURCE_NOT_CONFIGURED",
            detail="module source provisioning is not wired",
        )
    try:
        return factory(session)
    except AppError:
        raise
    except Exception as exc:
        raise ModuleSourceUnavailableError(
            code="MODULE_SOURCE_NOT_CONFIGURED",
            detail="module source provisioning factory failed closed",
        ) from exc


@router.post(
    "",
    response_model=ModuleSourceResponse,
    status_code=201,
    responses={
        403: {"description": "Company/plugin administration denied"},
        404: {"description": "Company not found"},
        409: {"description": "Immutable source/provider identity conflict"},
        422: {"description": "Strict request, manifest, or plugin id validation failed"},
        503: {"description": "Composition, provider, or persistence unavailable"},
    },
)
async def create_module_source(
    company_id: CompanyId,
    plugin_id: PluginId,
    body: CreateModuleSourceBody,
    request: Request,
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> ModuleSourceResponse:
    response.headers["Cache-Control"] = "no-store"
    result = await _service(request, session).create_from_template(
        company_id=company_id,
        plugin_id=plugin_id,
        manifest=body.manifest,
        principal=principal,
        employee=employee,
    )
    response.status_code = 201 if result.created else 200
    return ModuleSourceResponse.from_source(result.source)


@router.put(
    "/scope",
    response_model=ModuleSourceResponse,
    responses={
        403: {"description": "Company/project access denied"},
        404: {"description": "Company, source, or project not found"},
        409: {"description": "Immutable scope conflict"},
        422: {"description": "Invalid cabinet/project scope"},
        503: {"description": "Scope persistence unavailable"},
    },
)
async def bind_module_source_scope(
    company_id: CompanyId,
    plugin_id: PluginId,
    body: BindModuleSourceScopeBody,
    request: Request,
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> ModuleSourceResponse:
    response.headers["Cache-Control"] = "no-store"
    source = await _service(request, session).bind_scope(
        company_id=company_id,
        plugin_id=plugin_id,
        cabinet_id=body.cabinet_id,
        project_id=body.project_id,
        principal=principal,
        employee=employee,
    )
    response.status_code = 200
    return ModuleSourceResponse.from_source(source)


@router.get(
    "",
    response_model=ModuleSourceResponse,
    responses={
        403: {"description": "Company/plugin administration denied"},
        404: {"description": "Company or module source not found"},
        422: {"description": "Invalid company/plugin path"},
        503: {"description": "Composition or persistence unavailable"},
    },
)
async def get_module_source_status(
    company_id: CompanyId,
    plugin_id: PluginId,
    request: Request,
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> ModuleSourceResponse:
    response.headers["Cache-Control"] = "no-store"
    source = await _service(request, session).get_status(
        company_id=company_id,
        plugin_id=plugin_id,
        principal=principal,
        employee=employee,
    )
    response.status_code = 200
    return ModuleSourceResponse.from_source(source)


__all__ = [
    "BindModuleSourceScopeBody",
    "CreateModuleSourceBody",
    "ModuleSourceResponse",
    "install_module_source_service_factory",
    "make_module_source_service_factory",
    "router",
]
