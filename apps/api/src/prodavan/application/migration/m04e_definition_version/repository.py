"""Trusted-server facade for definition/version evidence resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from prodavan.application.dynamic_apps_definition import (
    DefinitionResolution,
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionQuery,
)


@dataclass(frozen=True, slots=True)
class MigrationEvidenceContext:
    """Tenant identity must come from authenticated server state, never a request field."""

    resolved_tenant_id: str | None
    authenticated: bool
    active_typed_scope: bool
    m04_foundation_ready: bool
    m04e_foundation_ready: bool
    correlation_id: str


class DefinitionResolver(Protocol):
    async def resolve(self, query: DefinitionVersionQuery) -> DefinitionResolution: ...


class ServerDefinitionVersionRepository:
    """Composite resolution facade; exposes no raw version-ID lookup or write API."""

    def __init__(self, resolver: DefinitionResolver) -> None:
        self._resolver = resolver

    async def resolve(
        self,
        *,
        authenticated_context: MigrationEvidenceContext,
        asserted_tenant_id: str | None,
        app_id: str,
        manifest_version: str,
        expected_artifact_digest: str | None = None,
    ) -> DefinitionResolution:
        tenant_id = authenticated_context.resolved_tenant_id
        if (
            not authenticated_context.authenticated or not tenant_id
            or not authenticated_context.active_typed_scope
            or not authenticated_context.m04_foundation_ready
            or not authenticated_context.m04e_foundation_ready
            or not authenticated_context.correlation_id.strip()
        ):
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_DEFINITION_HISTORY)
        if asserted_tenant_id is not None and asserted_tenant_id != tenant_id:
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.CROSS_TENANT_DEFINITION)
        if not app_id.strip() or not manifest_version.strip():
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.INVALID_VERSION_HISTORY)
        if expected_artifact_digest is not None and not expected_artifact_digest.startswith("sha256:"):
            return self._negative(DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT)
        return await self._resolver.resolve(DefinitionVersionQuery(
            tenant_id=tenant_id,
            app_id=app_id,
            manifest_version=manifest_version,
            expected_artifact_digest=expected_artifact_digest,
        ))

    @staticmethod
    def _negative(outcome: DefinitionResolutionOutcome, reason: DefinitionResolutionReason) -> DefinitionResolution:
        return DefinitionResolution(outcome=outcome, reason=reason)


__all__ = ["MigrationEvidenceContext", "ServerDefinitionVersionRepository"]
