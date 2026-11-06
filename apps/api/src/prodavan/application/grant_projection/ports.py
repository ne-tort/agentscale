"""Decision and repository-owned atomic transition ports."""
from __future__ import annotations

from typing import Protocol

from prodavan.application.canonical_grants import CanonicalGrantDecisionResult, CanonicalGrantRequest
from prodavan.application.dynamic_apps_definition import DefinitionResolution, DefinitionVersionQuery
from prodavan.application.plugin_instances import PluginInstanceIdentity, PluginInstanceWriteRequest, PositiveWriteDecision

from .contracts import GrantProjectionCommand


class DefinitionDecisionPort(Protocol):
    async def resolve(self, query: DefinitionVersionQuery) -> DefinitionResolution: ...


class RuntimeFencingDecisionPort(Protocol):
    async def resolve_identity(self, *, plugin_instance_id: str, tenant_id: str, app_id: str, app_version_id: str) -> PluginInstanceIdentity | None: ...

    async def authorize_positive_write(self, request: PluginInstanceWriteRequest) -> PositiveWriteDecision: ...


class CanonicalGrantDecisionPort(Protocol):
    async def authorize(self, request: CanonicalGrantRequest, *, now=None) -> CanonicalGrantDecisionResult: ...


class AtomicGrantProjectionPort(Protocol):
    """Only repository-owned seam for a canonical projection transition."""

    async def project(self, command: GrantProjectionCommand) -> bool: ...


__all__ = [
    "AtomicGrantProjectionPort", "CanonicalGrantDecisionPort", "DefinitionDecisionPort",
    "RuntimeFencingDecisionPort",
]
