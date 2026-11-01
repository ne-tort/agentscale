"""Ports used by the dynamic plugin capability facade."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from prodavan.domain.dynamic_apps import AppScope

from .contracts import PluginInvokeRequest, PluginOpenRequest, PluginRecord, QuarantineEvidence


class DynamicPluginRepository(Protocol):
    async def resolve(self, request: PluginOpenRequest) -> PluginRecord | None: ...

    async def get_grants(self, *, instance_id: str, app_version_id: str) -> object: ...

    async def get_instance(self, *, instance_id: str, scope: AppScope) -> object: ...


class CapabilityExecutor(Protocol):
    async def invoke(
        self,
        *,
        capability: str,
        operation: str,
        resource: str,
        params: Mapping[str, Any],
        context: object,
    ) -> Any: ...


class CapabilityQuotaPort(Protocol):
    async def allows(self, *, tenant_id: str, instance_id: str, request: PluginInvokeRequest) -> bool: ...


class CapabilityScopePort(Protocol):
    async def allows(self, *, tenant_id: str, subject_id: str, scope: AppScope, request: PluginInvokeRequest) -> bool: ...


class PluginQuarantinePort(Protocol):
    async def append(self, evidence: QuarantineEvidence) -> None: ...


__all__ = [
    "CapabilityExecutor", "CapabilityQuotaPort", "CapabilityScopePort",
    "DynamicPluginRepository", "PluginQuarantinePort",
]
