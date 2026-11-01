"""Fail-closed canonical grant projection decision adapter."""
from __future__ import annotations

from datetime import UTC, datetime

from prodavan.application.canonical_grants import CanonicalGrantDecision, CanonicalGrantRequest
from prodavan.application.dynamic_apps_definition import DefinitionResolutionOutcome, DefinitionVersionQuery

from .contracts import (
    GrantProjectionCommand,
    GrantProjectionDecision,
    GrantProjectionReason,
    GrantProjectionRequest,
    GrantProjectionResult,
)
from .ports import (
    AtomicGrantProjectionPort,
    CanonicalGrantDecisionPort,
    DefinitionDecisionPort,
    RuntimeFencingDecisionPort,
)


class GrantProjectionDecisionAdapter:
    """Compose M04.e, M05 and M06 decisions without owning persistence."""

    def __init__(
        self,
        *,
        definitions: DefinitionDecisionPort,
        fencing: RuntimeFencingDecisionPort,
        grants: CanonicalGrantDecisionPort,
        transition: AtomicGrantProjectionPort | None = None,
    ) -> None:
        self._definitions = definitions
        self._fencing = fencing
        self._grants = grants
        self._transition = transition

    async def decide(self, request: GrantProjectionRequest, *, now: datetime | None = None) -> GrantProjectionResult:
        checked_at = now or datetime.now(UTC)
        if request.source_kind != "canonical":
            return await self._finish(request, GrantProjectionDecision.QUARANTINE, GrantProjectionReason.LEGACY_SOURCE if request.source_kind == "legacy" else GrantProjectionReason.UNSUPPORTED_SOURCE)
        if request.source_schema_version != "canonical.v1":
            return await self._finish(request, GrantProjectionDecision.QUARANTINE, GrantProjectionReason.UNSUPPORTED_SOURCE)
        if _wildcard(request.capability_key, request.operation, request.resource, request.domain):
            return await self._finish(request, GrantProjectionDecision.QUARANTINE, GrantProjectionReason.WILDCARD_SOURCE)

        resolution = await self._definitions.resolve(DefinitionVersionQuery(
            tenant_id=request.tenant_id,
            app_id=request.app_id,
            manifest_version=request.manifest_version,
            expected_artifact_digest=request.fencing.artifact_digest,
        ))
        if resolution.outcome is DefinitionResolutionOutcome.QUARANTINED:
            return await self._finish(request, GrantProjectionDecision.QUARANTINE, GrantProjectionReason.DEFINITION_QUARANTINED)
        if resolution.outcome is not DefinitionResolutionOutcome.RESOLVED or resolution.reference is None or resolution.version is None:
            return await self._finish(request, GrantProjectionDecision.DENY, GrantProjectionReason.DEFINITION_UNRESOLVED)
        definition = resolution.reference
        if definition.tenant_id != request.tenant_id or definition.app_id != request.app_id:
            return await self._finish(request, GrantProjectionDecision.DENY, GrantProjectionReason.DEFINITION_UNRESOLVED, definition=definition)
        if definition.artifact_digest != request.fencing.artifact_digest:
            return await self._finish(request, GrantProjectionDecision.QUARANTINE, GrantProjectionReason.UNSUPPORTED_SOURCE, definition=definition)

        identity = await self._fencing.resolve_identity(
            plugin_instance_id=request.plugin_instance_id,
            tenant_id=request.tenant_id,
            app_id=request.app_id,
            app_version_id=definition.app_version_id,
        )
        if identity is None or (
            identity.plugin_instance_id != request.plugin_instance_id
            or identity.definition_id != definition.app_definition_id
            or identity.version_id != definition.app_version_id
            or identity.tenant_id != request.tenant_id
            or identity.scope_kind != request.scope_kind
            or identity.scope_id != request.scope_id
            or identity.artifact_digest != definition.artifact_digest
            or definition.manifest_version != request.manifest_version
        ):
            return await self._finish(request, GrantProjectionDecision.DENY, GrantProjectionReason.RUNTIME_FENCING_DENIED, definition=definition)
        write_request = request.fencing.write_request(identity)
        fencing = await self._fencing.authorize_positive_write(write_request)
        if not fencing.allowed or fencing.record is None:
            return await self._finish(request, GrantProjectionDecision.DENY, GrantProjectionReason.RUNTIME_FENCING_DENIED, definition=definition)
        record = fencing.record
        if (
            record.identity != identity
            or record.subject_id != write_request.subject_id
            or record.session_id != write_request.session_id
            or record.state.value != "active"
            or record.fencing != write_request.fencing
            or record.lease_owner != write_request.lease_owner
            or record.lease_id != write_request.lease_id
        ):
            return await self._finish(request, GrantProjectionDecision.DENY, GrantProjectionReason.RUNTIME_FENCING_DENIED, definition=definition)

        requested = CanonicalGrantRequest(
            company_id=request.company_id,
            app_id=request.app_id,
            manifest_version=request.manifest_version,
            plugin_instance_id=request.plugin_instance_id,
            capability_key=request.capability_key,
            operation=request.operation,
            resource=request.resource,
            domain=request.domain,
            constraints=request.constraints,
            fencing=request.fencing,
            correlation_id=request.correlation_id or f"projection:{request.plugin_instance_id}:{request.session_id}",
        )
        grant = await self._grants.authorize(requested, now=checked_at)
        if grant.decision is CanonicalGrantDecision.QUARANTINE:
            reason = GrantProjectionReason.GRANT_QUARANTINED
            decision = GrantProjectionDecision.QUARANTINE
        elif not grant.allowed:
            reason = GrantProjectionReason.GRANT_DENIED
            decision = GrantProjectionDecision.DENY
        else:
            reason = GrantProjectionReason.ALLOWED
            decision = GrantProjectionDecision.ALLOW
        return await self._finish(request, decision, reason, definition=definition, grant=grant)

    async def _finish(self, request, decision, reason, *, definition=None, grant=None):
        result = GrantProjectionResult(request, decision, reason, definition, grant)
        if self._transition is None:
            return result
        projected = await self._transition.project(GrantProjectionCommand(request, decision, reason, definition, grant))
        return GrantProjectionResult(request, decision, reason, definition, grant, projected)


def _wildcard(*values: str) -> bool:
    return any(any(token in value for token in ("*", "?", "[", "]")) for value in values)


__all__ = ["GrantProjectionDecisionAdapter"]
