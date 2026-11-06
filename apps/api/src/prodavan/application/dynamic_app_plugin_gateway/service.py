"""Fail-closed dynamic app/plugin capability facade."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from prodavan.domain.dynamic_apps import AppLifecycleStatus, CapabilityDecision

from .contracts import (
    GatewayOutcome,
    GatewayReason,
    GatewayResult,
    PluginContext,
    PluginHandle,
    PluginInvokeRequest,
    PluginMethod,
    PluginOpenRequest,
    QuarantineEvidence,
)
from .ports import (
    CapabilityExecutor,
    CapabilityQuotaPort,
    CapabilityScopePort,
    DynamicPluginRepository,
    PluginQuarantinePort,
)


class DynamicAppPluginGatewayService:
    """Resolve a plugin once, then enforce every boundary on every invocation."""

    def __init__(
        self,
        repository: DynamicPluginRepository,
        *,
        executors: Mapping[str, CapabilityExecutor] | None = None,
        quota: CapabilityQuotaPort | None = None,
        scope: CapabilityScopePort | None = None,
        quarantine: PluginQuarantinePort | None = None,
    ) -> None:
        self._repository = repository
        self._executors = dict(executors or {})
        self._quota = quota
        self._scope = scope
        self._quarantine = quarantine
        self._contexts: dict[PluginHandle, PluginContext] = {}

    async def open(self, request: PluginOpenRequest) -> GatewayResult:
        if not self._valid_open(request):
            return self._negative(GatewayOutcome.DENIED, GatewayReason.INVALID_REQUEST)
        try:
            record = await self._repository.resolve(request)
        except Exception:
            return await self._quarantine_result(request, GatewayReason.PORT_FAILED, "resolution")
        if record is None:
            return self._negative(GatewayOutcome.MISSING, GatewayReason.APP_MISSING)
        definition, version, grants, instance = record.definition, record.version, record.grants, record.instance
        if definition.tenant_id != request.tenant_id or definition.scope != request.scope or instance.scope != request.scope:
            return await self._quarantine_result(request, GatewayReason.CROSS_TENANT, definition.id)
        if version.app_definition_id != definition.id or instance.app_definition_id != definition.id or instance.app_version_id != version.id:
            return await self._quarantine_result(request, GatewayReason.CROSS_VERSION, version.id)
        if definition.app_id != request.app_id or version.manifest_version != request.manifest_version:
            return await self._quarantine_result(request, GatewayReason.MANIFEST_INVALID, version.id)
        if definition.status is not AppLifecycleStatus.PUBLISHED or version.status is not AppLifecycleStatus.PUBLISHED:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.LIFECYCLE_DENIED)
        if instance.status not in {AppLifecycleStatus.PUBLISHED}:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.LIFECYCLE_DENIED)
        methods = self._methods(version.manifest)
        if not methods:
            return await self._quarantine_result(request, GatewayReason.MANIFEST_INVALID, version.id)
        if not grants:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.GRANTS_MISSING)
        if any(
            row.app_definition_id != definition.id
            or row.app_version_id != version.id
            or row.scope != request.scope
            for row in grants
        ):
            return await self._quarantine_result(request, GatewayReason.CROSS_VERSION, "grants")
        context = PluginContext(
            tenant_id=request.tenant_id,
            app_id=request.app_id,
            app_definition_id=definition.id,
            app_version_id=version.id,
            manifest_version=version.manifest_version,
            artifact_digest=version.artifact_digest,
            instance_id=instance.id,
            scope=request.scope,
            subject_id=request.subject_id,
            grants=tuple(grant for row in grants for grant in row.grants),
            requested_capabilities=frozenset(item.name.value for item in version.requested_capabilities),
            methods=methods,
            grant_rows=tuple(grants),
        )
        handle = PluginHandle()
        self._contexts[handle] = context
        return GatewayResult(outcome=GatewayOutcome.OPENED, handle=handle, context=context)

    async def invoke(self, request: PluginInvokeRequest, *, now: datetime | None = None) -> GatewayResult:
        checked_at = now or datetime.now(UTC)
        context = self._contexts.get(request.handle)
        if context is None:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.HANDLE_INVALID)
        try:
            current = await self._repository.resolve(
                PluginOpenRequest(
                    tenant_id=context.tenant_id,
                    app_id=context.app_id,
                    manifest_version=context.manifest_version,
                    scope=context.scope,
                    subject_id=context.subject_id,
                )
            )
        except Exception:
            return await self._quarantine_result_for_context(context, GatewayReason.PORT_FAILED)
        if current is None:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.HANDLE_INVALID)
        if not self._current_record_matches(context, current):
            return self._negative(GatewayOutcome.DENIED, GatewayReason.HANDLE_INVALID)
        method = next((item for item in context.methods if item.method == request.method), None)
        if method is None:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.METHOD_UNKNOWN)
        if method.capability not in context.requested_capabilities:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.CAPABILITY_NOT_REQUESTED)
        if any(row.expires_at is not None and checked_at >= row.expires_at for row in context.grant_rows):
            return self._negative(GatewayOutcome.DENIED, GatewayReason.GRANT_DENIED)
        grant = next(
            (
                item
                for item in context.grants
                if item.name.value == method.capability
                and item.decision is CapabilityDecision.GRANTED
                and (item.expires_at is None or checked_at < item.expires_at)
            ),
            None,
        )
        if grant is None:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.GRANT_DENIED)
        if not self._grant_matches_method(grant, method, request):
            return self._negative(GatewayOutcome.DENIED, GatewayReason.GRANT_DENIED)
        if not self._constraints_allow(method.constraints, request):
            return self._negative(GatewayOutcome.DENIED, GatewayReason.QUOTA_EXCEEDED, grant_id=None)
        try:
            scope_allowed = self._scope is not None and await self._scope.allows(tenant_id=context.tenant_id, subject_id=context.subject_id, scope=context.scope, request=request)
        except Exception:
            return await self._quarantine_result_for_context(context, GatewayReason.PORT_FAILED)
        if not scope_allowed:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.SCOPE_DENIED, grant_id=None)
        try:
            quota_allowed = self._quota is not None and await self._quota.allows(tenant_id=context.tenant_id, instance_id=context.instance_id, request=request)
        except Exception:
            return await self._quarantine_result_for_context(context, GatewayReason.PORT_FAILED)
        if not quota_allowed:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.QUOTA_EXCEEDED)
        executor = self._executors.get(method.capability)
        if executor is None:
            return self._negative(GatewayOutcome.DENIED, GatewayReason.EXECUTOR_MISSING)
        try:
            value = await executor.invoke(capability=method.capability, operation=method.operation, resource=method.resource, params=request.params, context=context)
        except Exception:
            return await self._quarantine_result_for_context(context, GatewayReason.EXECUTOR_FAILED)
        return GatewayResult(outcome=GatewayOutcome.ALLOWED, value=value, grant_id=None)

    @staticmethod
    def _valid_open(request: PluginOpenRequest) -> bool:
        return bool(request.tenant_id.strip() and request.app_id.strip() and request.manifest_version.strip() and request.subject_id.strip() and request.scope.id.strip())

    @staticmethod
    def _current_record_matches(context: PluginContext, record: object) -> bool:
        try:
            return (
                record.definition.id == context.app_definition_id
                and record.definition.tenant_id == context.tenant_id
                and record.definition.app_id == context.app_id
                and record.definition.scope == context.scope
                and record.definition.status is AppLifecycleStatus.PUBLISHED
                and record.version.id == context.app_version_id
                and record.version.app_definition_id == context.app_definition_id
                and record.version.manifest_version == context.manifest_version
                and record.version.artifact_digest == context.artifact_digest
                and record.version.status is AppLifecycleStatus.PUBLISHED
                and record.instance.id == context.instance_id
                and record.instance.app_definition_id == context.app_definition_id
                and record.instance.app_version_id == context.app_version_id
                and record.instance.scope == context.scope
                and record.instance.status is AppLifecycleStatus.PUBLISHED
                and frozenset(item.name.value for item in record.version.requested_capabilities) == context.requested_capabilities
                and DynamicAppPluginGatewayService._methods(record.version.manifest) == context.methods
                and tuple(grant for row in record.grants for grant in row.grants) == context.grants
                and tuple(record.grants) == context.grant_rows
            )
        except (AttributeError, TypeError):
            return False

    @staticmethod
    def _grant_matches_method(grant: object, method: PluginMethod, request: PluginInvokeRequest) -> bool:
        try:
            constraints = grant.constraints or {}
            return (
                isinstance(constraints, Mapping)
                and DynamicAppPluginGatewayService._exact_constraint(constraints, "operations", method.operation)
                and DynamicAppPluginGatewayService._exact_constraint(constraints, "resources", method.resource)
                and DynamicAppPluginGatewayService._exact_constraint(constraints, "domains", method.domain)
                and DynamicAppPluginGatewayService._constraints_allow(constraints, request)
            )
        except (AttributeError, TypeError):
            return False

    @staticmethod
    def _exact_constraint(constraints: Mapping[str, Any], key: str, expected: str) -> bool:
        value = constraints.get(key)
        return isinstance(value, (tuple, list)) and value == [expected] or value == (expected,)

    @staticmethod
    def _constraints_allow(constraints: Mapping[str, Any], request: PluginInvokeRequest) -> bool:
        maximum = constraints.get("maxBytesPerRequest", constraints.get("max_bytes"))
        return isinstance(maximum, (int, float)) and maximum >= 0 and not (request.requested_bytes is None or request.requested_bytes > maximum) if maximum is not None else True

    @staticmethod
    def _methods(manifest: Mapping[str, Any]) -> tuple[PluginMethod, ...]:
        entrypoints = manifest.get("entrypoints")
        plugin = entrypoints.get("plugin") if isinstance(entrypoints, Mapping) else None
        raw = plugin.get("methods") if isinstance(plugin, Mapping) else None
        if not isinstance(raw, list):
            return ()
        result: list[PluginMethod] = []
        for item in raw:
            if not isinstance(item, Mapping) or not all(isinstance(item.get(key), str) and item[key].strip() for key in ("method", "capability", "operation", "resource", "domain")):
                return ()
            constraints = item.get("constraints", {})
            if not isinstance(constraints, Mapping) or not DynamicAppPluginGatewayService._json_constraints(constraints):
                return ()
            result.append(PluginMethod(method=item["method"], capability=item["capability"], operation=item["operation"], resource=item["resource"], domain=item["domain"], constraints=constraints))
        return tuple(result) if len({item.method for item in result}) == len(result) else ()

    @staticmethod
    def _json_constraints(value: object) -> bool:
        if value is None or isinstance(value, (str, bool, int, float)):
            return True
        if isinstance(value, Mapping):
            return all(isinstance(key, str) and key.strip() and DynamicAppPluginGatewayService._json_constraints(item) for key, item in value.items())
        if isinstance(value, (tuple, list)):
            return all(DynamicAppPluginGatewayService._json_constraints(item) for item in value)
        return False

    @staticmethod
    def _negative(outcome: GatewayOutcome, reason: GatewayReason, *, grant_id: str | None = None) -> GatewayResult:
        return GatewayResult(outcome=outcome, reason=reason, grant_id=grant_id)

    async def _quarantine_result(self, request: PluginOpenRequest, reason: GatewayReason, source_id: str) -> GatewayResult:
        evidence = QuarantineEvidence(reason=reason, tenant_id=request.tenant_id, app_id=request.app_id, source_id=source_id, observed_at=datetime.now(UTC))
        if self._quarantine is not None:
            await self._quarantine.append(evidence)
        return GatewayResult(outcome=GatewayOutcome.QUARANTINED, reason=reason)

    async def _quarantine_result_for_context(self, context: PluginContext, reason: GatewayReason) -> GatewayResult:
        evidence = QuarantineEvidence(reason=reason, tenant_id=context.tenant_id, app_id=context.app_id, source_id=context.instance_id, observed_at=datetime.now(UTC))
        if self._quarantine is not None:
            await self._quarantine.append(evidence)
        return GatewayResult(outcome=GatewayOutcome.QUARANTINED, reason=reason)


__all__ = ["DynamicAppPluginGatewayService"]
