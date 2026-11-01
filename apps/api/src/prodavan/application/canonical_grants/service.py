"""Fail-closed M06 canonical grants/gateway adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping, Sequence

from prodavan.application.dynamic_apps_definition.contracts import DefinitionResolutionOutcome, DefinitionVersionQuery

from .contracts import (
    CanonicalAuditEvent,
    CanonicalGrant,
    CanonicalGrantDecision,
    CanonicalGrantDecisionResult,
    CanonicalGrantReason,
    CanonicalGrantRequest,
)
from .ports import (
    AuthenticatedCompanyPort,
    CanonicalAuditPort,
    CanonicalGrantReadPort,
    DefinitionResolutionPort,
    PluginFencingDecisionPort,
    PluginIdentityReadPort,
)


class CanonicalGrantGatewayService:
    """Resolve identity and authorize a runtime call without persistence writes."""

    def __init__(self, *, company: AuthenticatedCompanyPort, definitions: DefinitionResolutionPort, grants: CanonicalGrantReadPort, plugin_identity: PluginIdentityReadPort, fencing: PluginFencingDecisionPort, audit: CanonicalAuditPort | None = None, supported_capability_keys: frozenset[str] | None = None) -> None:
        self._company = company
        self._definitions = definitions
        self._grants = grants
        self._plugin_identity = plugin_identity
        self._fencing = fencing
        self._audit = audit
        self._supported_capability_keys = supported_capability_keys

    async def authorize(self, request: CanonicalGrantRequest, *, now: datetime | None = None) -> CanonicalGrantDecisionResult:
        checked_at = now or datetime.now(UTC)
        if not _valid(request):
            return await self._finish(request, self._deny(request, CanonicalGrantReason.INVALID_REQUEST), checked_at)
        if self._supported_capability_keys is not None and request.capability_key not in self._supported_capability_keys:
            return await self._finish(request, self._deny(request, CanonicalGrantReason.CAPABILITY_UNKNOWN), checked_at)
        tenant_id = await self._company.resolve_tenant_id(company_id=request.company_id)
        if not tenant_id:
            return await self._finish(request, self._deny(request, CanonicalGrantReason.COMPANY_NOT_AUTHENTICATED), checked_at)
        resolution = await self._definitions.resolve(DefinitionVersionQuery(tenant_id=tenant_id, app_id=request.app_id, manifest_version=request.manifest_version))
        if resolution.outcome is not DefinitionResolutionOutcome.RESOLVED or resolution.reference is None or resolution.version is None:
            reason = CanonicalGrantReason.DEFINITION_UNRESOLVED
            return await self._finish(request, self._deny(request, reason, tenant_id=tenant_id), checked_at)
        reference = resolution.reference
        if reference.tenant_id != tenant_id or reference.app_id != request.app_id or reference.manifest_version != request.manifest_version:
            return await self._finish(request, self._deny(request, CanonicalGrantReason.DEFINITION_UNRESOLVED, tenant_id=tenant_id), checked_at)
        requested_capabilities = {item.name.value if hasattr(item.name, "value") else item.name for item in getattr(resolution.version, "requested_capabilities", ())}
        if request.capability_key not in requested_capabilities:
            return await self._finish(request, self._deny(request, CanonicalGrantReason.CAPABILITY_NOT_REQUESTED, tenant_id=tenant_id, app_version_id=reference.app_version_id), checked_at)
        if request.fencing is None or request.fencing.plugin_instance_id != request.plugin_instance_id or request.fencing.artifact_digest != reference.artifact_digest:
            return await self._finish(request, self._deny(request, CanonicalGrantReason.PLUGIN_FENCING_DENIED, tenant_id=tenant_id, app_version_id=reference.app_version_id), checked_at)
        identity = await self._plugin_identity.resolve_identity(plugin_instance_id=request.plugin_instance_id, tenant_id=tenant_id, app_id=request.app_id, app_version_id=reference.app_version_id)
        if identity is None or identity.plugin_instance_id != request.plugin_instance_id or identity.definition_id != reference.app_definition_id or identity.tenant_id != tenant_id or identity.version_id != reference.app_version_id or identity.artifact_digest != reference.artifact_digest:
            return await self._finish(request, self._deny(request, CanonicalGrantReason.PLUGIN_FENCING_DENIED, tenant_id=tenant_id, app_version_id=reference.app_version_id), checked_at)
        fencing_decision = await self._fencing.authorize_positive_write(request.fencing.write_request(identity))
        if not fencing_decision.allowed or fencing_decision.record is None:
            return await self._finish(request, self._deny(request, CanonicalGrantReason.PLUGIN_FENCING_DENIED, tenant_id=tenant_id, app_version_id=reference.app_version_id), checked_at)
        record = fencing_decision.record
        if record.identity != identity or record.subject_id != request.fencing.subject_id or record.session_id != request.fencing.session_id or record.state.value != "active" or record.fencing.runtime_generation != request.fencing.runtime_generation or record.fencing.writer_generation != request.fencing.writer_generation or record.lease_owner != request.fencing.lease_owner or record.lease_id != request.fencing.lease_id or record.lease_until is None or record.lease_until <= checked_at:
            return await self._finish(request, self._deny(request, CanonicalGrantReason.PLUGIN_FENCING_DENIED, tenant_id=tenant_id, app_version_id=reference.app_version_id), checked_at)

        candidates: Sequence[CanonicalGrant] = await self._grants.list_grants(tenant_id=tenant_id, app_id=request.app_id, app_version_id=reference.app_version_id, plugin_instance_id=request.plugin_instance_id)
        if not candidates:
            return await self._finish(request, self._deny(request, CanonicalGrantReason.GRANT_MISSING, tenant_id=tenant_id, app_version_id=reference.app_version_id), checked_at)
        first_failure: tuple[CanonicalGrantReason, str] | None = None
        for grant in candidates:
            if not self._representable(grant):
                return await self._finish(request, self._deny(request, CanonicalGrantReason.UNREPRESENTABLE_GRANT, tenant_id=tenant_id, app_version_id=reference.app_version_id), checked_at)
            if grant.tenant_id != tenant_id or grant.app_definition_id != reference.app_definition_id or grant.app_id != request.app_id or grant.app_version_id != reference.app_version_id or grant.app_artifact_digest != reference.artifact_digest or grant.scope_kind != identity.scope_kind or grant.scope_id != identity.scope_id or grant.plugin_instance_id != request.plugin_instance_id:
                continue
            if grant.capability_key != request.capability_key:
                continue
            reason = grant.check(operation=request.operation, resource=request.resource, domain=request.domain, constraints=request.constraints, subject_id=request.fencing.subject_id, session_id=request.fencing.session_id, now=checked_at)
            if reason is None:
                result = CanonicalGrantDecisionResult(CanonicalGrantDecision.ALLOW, CanonicalGrantReason.ALLOWED, request.correlation_id, grant_id=grant.grant_id, tenant_id=tenant_id, app_version_id=reference.app_version_id)
                return await self._finish(request, result, checked_at)
            if first_failure is None:
                first_failure = (reason, grant.grant_id)
        if first_failure is not None:
            reason, grant_id = first_failure
            return await self._finish(request, self._deny(request, reason, tenant_id=tenant_id, app_version_id=reference.app_version_id, grant_id=grant_id), checked_at)
        return await self._finish(request, self._deny(request, CanonicalGrantReason.GRANT_MISSING, tenant_id=tenant_id, app_version_id=reference.app_version_id), checked_at)

    async def _finish(self, request: CanonicalGrantRequest, result: CanonicalGrantDecisionResult, now: datetime) -> CanonicalGrantDecisionResult:
        if self._audit is not None:
            await self._audit.record(CanonicalAuditEvent(correlation_id=request.correlation_id, occurred_at=now, company_id=request.company_id, tenant_id=result.tenant_id, app_id=request.app_id, manifest_version=request.manifest_version, app_version_id=result.app_version_id, plugin_instance_id=request.plugin_instance_id, capability_key=request.capability_key, operation=request.operation, resource=request.resource, domain=request.domain, decision=result.decision, reason=result.reason, grant_id=result.grant_id))
        return result

    @staticmethod
    def _deny(request: CanonicalGrantRequest, reason: CanonicalGrantReason, *, tenant_id: str | None = None, app_version_id: str | None = None, grant_id: str | None = None) -> CanonicalGrantDecisionResult:
        decision = CanonicalGrantDecision.QUARANTINE if reason in {CanonicalGrantReason.UNSUPPORTED_GRANT, CanonicalGrantReason.WILDCARD_GRANT, CanonicalGrantReason.UNREPRESENTABLE_GRANT} else CanonicalGrantDecision.DENY
        return CanonicalGrantDecisionResult(decision, reason, request.correlation_id, grant_id=grant_id, tenant_id=tenant_id, app_version_id=app_version_id)

    @staticmethod
    def _representable(grant: CanonicalGrant) -> bool:
        values = (*grant.operations, *grant.resources, *grant.domains)
        return bool(
            grant.grant_id.strip()
            and grant.capability_key.strip()
            and values
            and all(value.strip() and not any(token in value for token in ("*", "?", "[", "]")) for value in values)
            and _json_constraint_shape(grant.constraints)
        )


def _json_constraint_shape(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return not isinstance(value, str) or not any(token in value for token in ("*", "?", "[", "]"))
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and key.strip() and _json_constraint_shape(item) for key, item in value.items())
    if isinstance(value, (tuple, list, frozenset)):
        return all(_json_constraint_shape(item) for item in value)
    return False


def _valid(request: CanonicalGrantRequest) -> bool:
    return all(value.strip() for value in (request.company_id, request.app_id, request.manifest_version, request.plugin_instance_id, request.capability_key, request.operation, request.resource, request.domain, request.correlation_id))


__all__ = ["CanonicalGrantGatewayService"]
