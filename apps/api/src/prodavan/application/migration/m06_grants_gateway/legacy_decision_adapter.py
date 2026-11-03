"""Side-effect-free decision projection of the current dynamic plugin gateway."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from prodavan.application.dynamic_app_plugin_gateway.contracts import (
    GatewayReason,
    PluginHandle,
    PluginInvokeRequest,
    PluginOpenRequest,
)
from prodavan.application.dynamic_app_plugin_gateway.ports import (
    CapabilityQuotaPort,
    CapabilityScopePort,
    DynamicPluginRepository,
)
from prodavan.application.dynamic_app_plugin_gateway.service import DynamicAppPluginGatewayService
from prodavan.domain.dynamic_apps import AppLifecycleStatus, AppScope, AppScopeKind, CapabilityDecision

from .adapter import IdentityResolutionError, LegacyDecisionPort, ResolvedGrantIdentity, ShadowDecision, binding_digest


class LegacyPluginGrantDecisionAdapter(LegacyDecisionPort):
    """Re-run current gateway checks using its repository and policy ports, no executor."""

    def __init__(self, *, repository: DynamicPluginRepository,
                 scope: CapabilityScopePort | None, quota: CapabilityQuotaPort | None) -> None:
        self._repository = repository
        self._scope = scope
        self._quota = quota

    async def decide(self, *, identity: ResolvedGrantIdentity, capability: str,
                     operation: str, resource: str, domain: str | None, now: datetime,
                     method: str | None = None, requested_bytes: int | None = None,
                     params: Mapping[str, Any] | None = None,
                     constraints: Mapping[str, Any] | None = None) -> ShadowDecision:
        binding = binding_digest(identity)
        if method is None or domain is None:
            return ShadowDecision(False, GatewayReason.INVALID_REQUEST.value, binding)
        request_params = params or {}
        if not isinstance(request_params, Mapping):
            return ShadowDecision(False, GatewayReason.INVALID_REQUEST.value, binding)
        try:
            scope = AppScope(kind=AppScopeKind(identity.installation_scope_kind),
                             id=identity.installation_scope_id)
            opened = await self._repository.resolve(PluginOpenRequest(
                tenant_id=identity.tenant_id, app_id=identity.app_id,
                manifest_version=identity.manifest_version, scope=scope,
                subject_id=identity.subject_id,
            ))
        except Exception as exc:
            raise IdentityResolutionError("legacy_grant_read_failed") from exc
        if opened is None:
            return ShadowDecision(False, GatewayReason.APP_MISSING.value, binding)
        definition, version, grant_rows, instance = opened.definition, opened.version, opened.grants, opened.instance
        if (definition.tenant_id != identity.tenant_id or definition.id != identity.app_definition_id
                or definition.app_id != identity.app_id or version.id != identity.app_version_id
                or version.app_definition_id != definition.id or version.manifest_version != identity.manifest_version
                or version.artifact_digest != identity.artifact_digest or instance.id != identity.plugin_instance_id
                or instance.app_definition_id != definition.id or instance.app_version_id != version.id
                or instance.scope != scope):
            return ShadowDecision(False, GatewayReason.CROSS_VERSION.value, binding)
        if (definition.status is not AppLifecycleStatus.PUBLISHED
                or version.status is not AppLifecycleStatus.PUBLISHED
                or instance.status is not AppLifecycleStatus.PUBLISHED):
            return ShadowDecision(False, GatewayReason.LIFECYCLE_DENIED.value, binding)
        if capability not in frozenset(item.name.value for item in version.requested_capabilities):
            return ShadowDecision(False, GatewayReason.CAPABILITY_NOT_REQUESTED.value, binding)
        if not grant_rows:
            return ShadowDecision(False, GatewayReason.GRANTS_MISSING.value, binding)
        if any(row.app_definition_id != definition.id or row.app_version_id != version.id or row.scope != scope
               for row in grant_rows):
            return ShadowDecision(False, GatewayReason.CROSS_VERSION.value, binding)
        if any(row.expires_at is not None and now >= row.expires_at for row in grant_rows):
            return ShadowDecision(False, "grant_expired", binding)
        methods = tuple(item for item in opened.methods if item.method == method)
        if not methods:
            return ShadowDecision(False, GatewayReason.METHOD_UNKNOWN.value, binding)
        if len(methods) != 1:
            return ShadowDecision(False, "method_ambiguous", binding)
        declaration = methods[0]
        if (declaration.capability != capability or declaration.operation != operation
                or declaration.resource != resource or declaration.domain != domain):
            return ShadowDecision(False, GatewayReason.GRANT_DENIED.value, binding)
        if not self._constraints_allow(declaration.constraints, constraints or {}, requested_bytes):
            return ShadowDecision(False, GatewayReason.QUOTA_EXCEEDED.value, binding)
        grant = next((item for row in grant_rows for item in row.grants
                      if item.name.value == capability
                      and item.decision is CapabilityDecision.GRANTED
                      and (item.expires_at is None or now < item.expires_at)), None)
        if grant is None:
            return ShadowDecision(False, GatewayReason.GRANT_DENIED.value, binding)
        request = PluginInvokeRequest(handle=PluginHandle(), method=method,
                                      params=request_params, requested_bytes=requested_bytes)
        if not DynamicAppPluginGatewayService._grant_matches_method(grant, declaration, request):
            return ShadowDecision(False, GatewayReason.GRANT_DENIED.value, binding)
        if self._scope is None:
            return ShadowDecision(False, GatewayReason.SCOPE_DENIED.value, binding)
        try:
            if not await self._scope.allows(tenant_id=identity.tenant_id, subject_id=identity.subject_id,
                                            scope=scope, request=request):
                return ShadowDecision(False, GatewayReason.SCOPE_DENIED.value, binding)
        except Exception as exc:
            raise IdentityResolutionError("legacy_scope_read_failed") from exc
        if self._quota is None:
            return ShadowDecision(False, GatewayReason.QUOTA_EXCEEDED.value, binding)
        try:
            if not await self._quota.allows(tenant_id=identity.tenant_id,
                                            instance_id=identity.plugin_instance_id, request=request):
                return ShadowDecision(False, GatewayReason.QUOTA_EXCEEDED.value, binding)
        except Exception as exc:
            raise IdentityResolutionError("legacy_quota_read_failed") from exc
        return ShadowDecision(True, "allowed", binding)

    @staticmethod
    def _constraints_allow(method_constraints: Mapping[str, Any], request_constraints: Mapping[str, Any],
                           requested_bytes: int | None) -> bool:
        if not isinstance(method_constraints, Mapping):
            return False
        maximum = method_constraints.get("maxBytesPerRequest", method_constraints.get("max_bytes"))
        if maximum is None:
            return True
        return (isinstance(maximum, (int, float)) and not isinstance(maximum, bool)
                and maximum >= 0 and requested_bytes is not None and requested_bytes <= maximum)


__all__ = ["LegacyPluginGrantDecisionAdapter"]
