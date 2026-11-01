"""Read/decision ports for the M06 canonical grant gateway."""

from __future__ import annotations

from typing import Protocol, Sequence

from prodavan.application.dynamic_apps_definition.contracts import DefinitionResolution, DefinitionVersionQuery
from prodavan.application.plugin_instances import PluginInstanceIdentity, PluginInstanceWriteRequest, PositiveWriteDecision

from .contracts import CanonicalAuditEvent, CanonicalGrant, CanonicalGrantRequest


class AuthenticatedCompanyPort(Protocol):
    async def resolve_tenant_id(self, *, company_id: str) -> str | None: ...


class DefinitionResolutionPort(Protocol):
    async def resolve(self, query: DefinitionVersionQuery) -> DefinitionResolution: ...


class CanonicalGrantReadPort(Protocol):
    async def list_grants(self, *, tenant_id: str, app_id: str, app_version_id: str, plugin_instance_id: str) -> Sequence[CanonicalGrant]: ...


class PluginIdentityReadPort(Protocol):
    async def resolve_identity(self, *, plugin_instance_id: str, tenant_id: str, app_id: str, app_version_id: str) -> PluginInstanceIdentity | None: ...


class PluginFencingDecisionPort(Protocol):
    async def authorize_positive_write(self, request: PluginInstanceWriteRequest) -> PositiveWriteDecision: ...


class CanonicalAuditPort(Protocol):
    async def record(self, event: CanonicalAuditEvent) -> None: ...


__all__ = ["AuthenticatedCompanyPort", "CanonicalAuditPort", "CanonicalGrantReadPort", "DefinitionResolutionPort", "PluginFencingDecisionPort", "PluginIdentityReadPort"]
