"""Company-facing and anonymous-public bootstrap decisions.

Only published artifact manifests and durable installation rows are trusted.
This module never returns claims tokens, DSN, secrets or raw admin receipts.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from prodavan.config.settings import settings
from prodavan.domain.plugins.origins import PluginOriginError, exact_plugin_origins
from prodavan.domain.errors import AppError
from prodavan.domain.plugins.types import (
    PLUGIN_CAPABILITY_ID_PATTERN,
    PLUGIN_CAPABILITY_VERSION_PATTERN,
    PLUGIN_OPERATION_PATTERN,
    PLUGIN_RPC_METHODS,
    PluginRpcOperations,
    RpcOperationDeclaration,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .contracts import PluginBootstrapResponse, PluginScopeResponse, RpcOperationResponse
from .repository import InstallationRows, SqlAlchemyPluginBootstrapRepository


def _invalid(detail: str) -> AppError:
    return AppError(code="PLUGIN_BOOTSTRAP_INVALID", title="Plugin bootstrap invalid", status=503, detail=detail)


def _not_connected() -> AppError:
    return AppError(code="PLUGIN_NOT_CONNECTED", title="Plugin not connected", status=404, detail="active verified installation not found")


def _route(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 512:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="invalid route_path")
    if any(char not in "/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in value):
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="invalid route_path")
    if any(part == ".." for part in value.split("/")) or (len(value) > 1 and value.endswith("/")):
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="route_path must be canonical")
    return value


def _configured_origins() -> tuple[str, str]:
    """Read the same server-owned pair used by Gateway static response policy."""
    try:
        return exact_plugin_origins(
            static_origin=settings.plugin_static_origin,
            shell_origin=settings.plugin_shell_origin,
        )
    except PluginOriginError as exc:
        raise _invalid(str(exc)) from exc


def _manifest_map(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid("published artifact manifest is not an object")
    return value


def _public_pages(manifest: dict[str, Any]) -> tuple[str, ...]:
    raw = manifest.get("public_pages", [])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise _invalid("manifest.public_pages is invalid")
    pages = tuple(_route(item) for item in raw)
    if len(set(pages)) != len(pages):
        raise _invalid("manifest.public_pages contains duplicates")
    return pages


def _rpc_operations(manifest: dict[str, Any]) -> PluginRpcOperations:
    raw = manifest.get("rpc_operations", [])
    if not isinstance(raw, list) or len(raw) > 64:
        raise _invalid("manifest.rpc_operations is invalid")
    operations: list[RpcOperationDeclaration] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"capability", "operation", "http_method", "backend_path", "public"}:
            raise _invalid("rpc operation has unknown fields")
        if not {"capability", "operation", "http_method", "backend_path"} <= set(item):
            raise _invalid("rpc operation is incomplete")
        capability = item["capability"]
        if not isinstance(capability, dict) or set(capability) != {"id", "version"}:
            raise _invalid("rpc operation capability is invalid")
        capability_id, capability_version = capability["id"], capability["version"]
        if not isinstance(capability_id, str) or not PLUGIN_CAPABILITY_ID_PATTERN.fullmatch(capability_id):
            raise _invalid("rpc operation capability.id is invalid")
        if not isinstance(capability_version, str) or not PLUGIN_CAPABILITY_VERSION_PATTERN.fullmatch(capability_version):
            raise _invalid("rpc operation capability.version is invalid")
        operation = item["operation"]
        method = item["http_method"]
        path = item["backend_path"]
        public = item.get("public", False)
        if not isinstance(operation, str) or not PLUGIN_OPERATION_PATTERN.fullmatch(operation):
            raise _invalid("rpc operation name is invalid")
        if not isinstance(method, str) or method not in PLUGIN_RPC_METHODS:
            raise _invalid("rpc operation method is invalid")
        if not isinstance(path, str) or not path.startswith("/api/") or any(part == ".." for part in path.split("/")):
            raise _invalid("rpc operation backend_path is invalid")
        if any(char not in "/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in path):
            raise _invalid("rpc operation backend_path is invalid")
        if not isinstance(public, bool):
            raise _invalid("rpc operation public is invalid")
        operations.append(RpcOperationDeclaration(capability_id, capability_version, operation, method, path, public))
    return PluginRpcOperations(tuple(operations))


def _frontend(manifest: dict[str, Any]) -> tuple[str, str]:
    frontend = manifest.get("frontend")
    if not isinstance(frontend, dict) or set(frontend) != {"entry", "base_path"}:
        raise _invalid("manifest.frontend is invalid")
    entry, base_path = frontend["entry"], frontend["base_path"]
    if (
        not isinstance(entry, str)
        or not entry
        or entry.startswith("/")
        or ".." in entry
        or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in entry)
    ):
        raise _invalid("manifest.frontend.entry is invalid")
    if (
        not isinstance(base_path, str)
        or not base_path.startswith("/")
        or any(part == ".." for part in base_path.split("/"))
        or any(char not in "/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in base_path)
    ):
        raise _invalid("manifest.frontend.base_path is invalid")
    return entry, base_path


def _validate_installation(rows: InstallationRows | None, company_id: str, plugin_id: str) -> InstallationRows:
    if rows is None or not rows.enabled:
        raise _not_connected()
    now = datetime.now(UTC)
    grant = rows.grant
    if (
        grant.status != "active"
        or grant.revoked_at is not None
        or (grant.expires_at is not None and grant.expires_at <= now)
        or grant.company_id != company_id
        or grant.plugin_id != plugin_id
    ):
        raise _not_connected()
    instance = rows.instance
    artifact = rows.artifact
    if (
        instance is None
        or artifact is None
        or instance.status != "active"
        or instance.company_id != company_id
        or instance.plugin_id != plugin_id
        or instance.manifest_version != grant.manifest_version
        or instance.artifact_digest != grant.artifact_digest
    ):
        raise _invalid("installation and grant do not match")
    if (
        artifact.status != "published"
        or artifact.cosign_verified is not True
        or artifact.published_at is None
        or artifact.plugin_id != plugin_id
        or artifact.version != grant.manifest_version
        or artifact.oci_digest != grant.artifact_digest
    ):
        raise _invalid("artifact is not a verified published artifact")
    manifest = _manifest_map(artifact.manifest)
    manifest_version = manifest.get("version") or manifest.get("manifest_version")
    if manifest.get("plugin_id") != plugin_id or manifest_version != grant.manifest_version:
        raise _invalid("manifest identity does not match installation")
    if manifest.get("protocol") != "plugin-rpc/1":
        raise _invalid("manifest protocol is unsupported")
    return rows


def _scope_unavailable() -> AppError:
    return AppError(
        code="PLUGIN_BOOTSTRAP_SCOPE_UNAVAILABLE",
        title="Plugin bootstrap scope unavailable",
        status=503,
        detail=(
            "authoritative cabinet_id/project_id binding is required for private "
            "bootstrap; query/body/Host and tenant-wide defaults are forbidden"
        ),
    )


def _response_scope(rows: InstallationRows, *, public: bool) -> PluginScopeResponse:
    grant = rows.grant
    if public:
        # Anonymous public operations never inherit private cabinet/project scope.
        return PluginScopeResponse(company_id=grant.company_id)
    # Private RPC has no tenant-wide scope fallback: exact cabinet/project
    # evidence is required. Anonymous public responses intentionally omit both.
    scope = rows.scope
    if scope is None:
        raise _scope_unavailable()
    if (
        scope.company_id != grant.company_id
        or scope.plugin_instance_id != grant.plugin_instance_id
    ):
        raise AppError(
            code="PLUGIN_BOOTSTRAP_SCOPE_MISMATCH",
            title="Plugin bootstrap scope mismatch",
            status=503,
            detail="installation scope tenant/instance does not match the active grant",
        )
    cabinet_id = scope.cabinet_id
    project_id = scope.project_id
    if (
        not isinstance(cabinet_id, str)
        or not cabinet_id.strip()
        or cabinet_id != cabinet_id.strip()
        or not isinstance(project_id, str)
        or not project_id.strip()
        or project_id != project_id.strip()
    ):
        raise _scope_unavailable()
    return PluginScopeResponse(
        company_id=grant.company_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
    )


def _operation_response(operation: RpcOperationDeclaration) -> RpcOperationResponse:
    return RpcOperationResponse(
        capability_id=operation.capability_id,
        capability_version=operation.capability_version,
        operation=operation.operation,
        http_method=operation.http_method,
        backend_path=operation.backend_path,
        public=operation.public,
    )


class PluginBootstrapService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def _repository(self, session: AsyncSession) -> SqlAlchemyPluginBootstrapRepository:
        return SqlAlchemyPluginBootstrapRepository(session)

    def _response(self, rows: InstallationRows, *, route_path: str, public: bool) -> PluginBootstrapResponse:
        grant = rows.grant
        scope = _response_scope(rows, public=public)
        artifact = rows.artifact
        manifest = _manifest_map(artifact.manifest)
        pages = _public_pages(manifest)
        if public and route_path not in pages:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="route_path is not a declared public page")
        operations = _rpc_operations(manifest)
        selected = operations.public_operations() if public else operations.operations
        operation_capabilities = [f"{item.capability_id}@{item.capability_version}" for item in selected]
        if public:
            capabilities = operation_capabilities
        else:
            raw_grants = grant.capabilities
            if not isinstance(raw_grants, list) or any(not isinstance(item, str) or not item for item in raw_grants):
                raise _invalid("grant capabilities are invalid")
            capabilities = list(dict.fromkeys([*raw_grants, *operation_capabilities]))
        entry, base_path = _frontend(manifest)
        static_origin, shell_origin = _configured_origins()
        return PluginBootstrapResponse(
            plugin_id=grant.plugin_id,
            manifest_version=grant.manifest_version,
            artifact_digest=grant.artifact_digest,
            static_origin=static_origin,
            plugin_shell_origin=shell_origin,
            frontend_entry=entry,
            frontend_base_path=base_path,
            route_path=route_path,
            capabilities=capabilities,
            rpc_operations=[_operation_response(item) for item in selected],
            public_pages=list(pages),
            scope=scope,
            grant_expires_at=grant.expires_at,
        )

    async def private(self, *, employee: EmployeeRow, company_id: str, plugin_id: str, route_path: str = "/") -> PluginBootstrapResponse:
        route_path = _route(route_path)
        async with self._session_factory() as session:
            repository = self._repository(session)
            await repository.require_membership(employee, company_id)
            rows = _validate_installation(await repository.installation(company_id, plugin_id), company_id, plugin_id)
            scope = await repository.installation_scope(rows)
            rows = replace(rows, scope=scope)
        return self._response(rows, route_path=route_path, public=False)

    async def public(self, *, company_id: str, plugin_id: str, route_path: str = "/") -> PluginBootstrapResponse:
        route_path = _route(route_path)
        async with self._session_factory() as session:
            repository = self._repository(session)
            if await repository.active_company(company_id) is None:
                raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")
            rows = _validate_installation(await repository.installation(company_id, plugin_id), company_id, plugin_id)
        return self._response(rows, route_path=route_path, public=True)


__all__ = ["PluginBootstrapService"]
