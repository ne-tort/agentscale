"""Resolve M06 identity using the existing server-side M04/M04E/M05 facades."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from typing import Any

from prodavan.application.canonical_grants.ports import AuthenticatedCompanyPort
from prodavan.application.dynamic_apps_definition.contracts import DefinitionResolutionOutcome
from prodavan.application.migration.m04_scope.scope_migration import (
    MigrationEvidenceContext as M04EvidenceContext,
    ResolveScopePort,
    ScopeResolution,
    ScopeResolutionEvidence,
    evaluate_active_scope_evidence,
)
from prodavan.application.migration.m04e_definition_version.repository import (
    MigrationEvidenceContext as M04EResolverContext,
    ServerDefinitionVersionRepository,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    FailClosedResolvePluginInstance,
    InstanceIdentity,
    InstanceLifecycleState,
    InstanceResolveQuery,
    M05UpstreamEvidence,
    RuntimeAvailability,
    TrustedRuntimeContext,
)
from prodavan.application.scope_registry.contracts import ScopeAnchors, ScopeKind

from .adapter import (
    AuthenticatedGrantContext,
    DependencyGates,
    IdentityResolutionError,
    IdentityResolutionPort,
    LegacyGrantSource,
    ResolvedGrantIdentity,
    digest,
    is_sha256,
)


class ApplicationIdentityResolutionAdapter(IdentityResolutionPort):
    """Resolve tenant/scope, immutable definition/version and exact active instance."""

    def __init__(self, *, company: AuthenticatedCompanyPort, m04: ResolveScopePort,
                 m04e: ServerDefinitionVersionRepository,
                 m05: FailClosedResolvePluginInstance, m05_upstream: M05UpstreamEvidence) -> None:
        self._company, self._m04, self._m04e = company, m04, m04e
        self._m05, self._m05_upstream = m05, m05_upstream

    async def resolve(self, *, context: AuthenticatedGrantContext, app_id: str,
                      manifest_version: str, source: LegacyGrantSource,
                      now: datetime | None = None,
                      dependency_gates: DependencyGates) -> ResolvedGrantIdentity:
        checked_at = now or datetime.now(UTC)
        if (not context.authenticated or not context.company_id or not context.tenant_id
                or not context.subject_id or not context.subject_kind or not context.session_id
                or not context.transaction_id or context.rls_context is None
                or not context.correlation_id.strip() or checked_at.tzinfo is None
                or checked_at.utcoffset() is None or app_id != source.app_id
                or manifest_version != source.manifest_version
                or not source.app_definition_id or not source.app_version_id
                or context.app_definition_id != source.app_definition_id):
            raise IdentityResolutionError("authenticated_m04_m04e_m05_context_missing")
        if not dependency_gates.positive_projection_ready:
            raise IdentityResolutionError("m06_upstream_or_schema_handoff_blocked")
        if not _handoff_matches(dependency_gates, context, source):
            raise IdentityResolutionError("m06_handoff_identity_mismatch")
        try:
            tenant_id = await self._company.resolve_tenant_id(company_id=context.company_id)
        except Exception as exc:
            raise IdentityResolutionError("authenticated_company_resolution_failed") from exc
        if tenant_id != context.tenant_id:
            raise IdentityResolutionError("authenticated_tenant_binding_mismatch")

        anchors = _scope_anchors(context, source, tenant_id)
        rls = context.rls_context
        if (rls.tenant_id != tenant_id or rls.user_id != context.subject_id
                or rls.cabinet_id != context.cabinet_id or rls.project_id != context.project_id):
            raise IdentityResolutionError("m04_rls_context_mismatch")
        m04_context = M04EvidenceContext(
            tenant_id=tenant_id, authenticated=True, transaction_id=context.transaction_id,
            correlation_id=context.correlation_id, user_id=context.subject_id,
            role=rls.role, rls_context=rls, writer_generation=context.writer_generation,
        )
        if not m04_context.usable or not m04_context.matches_scope(anchors):
            raise IdentityResolutionError("m04_transaction_rls_or_writer_context_unusable")
        try:
            scope_evidence = await self._m04.resolve_active(context=m04_context, anchors=anchors)
        except Exception as exc:
            raise IdentityResolutionError("m04_scope_resolution_failed") from exc
        scope_result = evaluate_active_scope_evidence(
            context=m04_context, anchors=anchors, evidence=scope_evidence,
            correlation_id=context.correlation_id,
        )
        if scope_result.outcome is not ScopeResolution.ACTIVE or scope_result.entry is None:
            raise IdentityResolutionError("m04_scope_membership_or_rls_unresolved")
        if not isinstance(scope_evidence, ScopeResolutionEvidence):
            raise IdentityResolutionError("m04_scope_evidence_missing")
        scope_digest = digest({
            "anchors": anchors.model_dump(mode="json"),
            "entry": _json_value(scope_evidence.entry),
            "parent_proof": _json_value(scope_evidence.parent_proof),
            "parent_compatibility_transaction_id": scope_evidence.parent_compatibility_transaction_id,
            "parent_evidence": _json_value(scope_evidence.parent_evidence),
            "rls_evidence": _json_value(scope_evidence.rls_evidence),
            "membership_evidence": _json_value(scope_evidence.actor_membership_evidence),
            "transaction_id": context.transaction_id,
            "correlation_id": context.correlation_id,
        })
        rls_digest = digest(_json_value(scope_evidence.rls_evidence))

        m04e_context = M04EResolverContext(
            resolved_tenant_id=tenant_id, authenticated=True,
            transaction_id=context.transaction_id, correlation_id=context.correlation_id,
        )
        try:
            definition_result = await self._m04e.resolve(
                authenticated_context=m04e_context, asserted_tenant_id=tenant_id,
                app_id=app_id, manifest_version=manifest_version,
            )
        except Exception as exc:
            raise IdentityResolutionError("m04e_definition_version_resolution_failed") from exc
        reference = definition_result.reference
        version = definition_result.version
        if (definition_result.outcome is not DefinitionResolutionOutcome.RESOLVED
                or reference is None or version is None or definition_result.definition is None):
            raise IdentityResolutionError("m04e_definition_or_version_unresolved")
        definition = definition_result.definition
        if (reference.tenant_id != tenant_id or reference.app_id != app_id
                or reference.app_definition_id != source.app_definition_id
                or reference.app_version_id != source.app_version_id
                or reference.manifest_version != manifest_version
                or definition.scope.kind.value != anchors.scope_kind.value
                or definition.scope.id != anchors.scope_id
                or version.app_definition_id != reference.app_definition_id
                or version.id != reference.app_version_id
                or version.manifest_version != reference.manifest_version
                or version.artifact_digest != reference.artifact_digest):
            raise IdentityResolutionError("m04e_exact_definition_version_mismatch")
        definition_digest = definition_result.evidence.get("resolution_digest")
        if not is_sha256(definition_digest) or not is_sha256(definition_result.evidence.get("owner_digest")):
            raise IdentityResolutionError("m04e_evidence_digest_missing")
        requested = frozenset(
            item.name.value if hasattr(item.name, "value") else str(item.name)
            for item in version.requested_capabilities
        )
        if any(not item.strip() for item in requested):
            raise IdentityResolutionError("m04e_requested_capabilities_missing")

        if (not context.plugin_instance_id or context.runtime_generation is None
                or context.writer_generation is None
                or not self._m05_upstream.ready_for_positive_transition(
                    (tenant_id, reference.app_definition_id))):
            raise IdentityResolutionError("m05_instance_or_upstream_handoff_blocked")
        instance_scope = ScopeAnchors(
            scope_kind=ScopeKind.PLUGIN_INSTANCE, scope_id=context.plugin_instance_id,
            tenant_id=tenant_id, cabinet_id=anchors.cabinet_id, project_id=anchors.project_id,
            plugin_instance_id=context.plugin_instance_id,
        )
        identity = InstanceIdentity(
            plugin_instance_id=context.plugin_instance_id,
            definition_id=reference.app_definition_id, version_id=reference.app_version_id,
            app_id=reference.app_id, manifest_version=reference.manifest_version,
            tenant_id=tenant_id, scope_kind=ScopeKind.PLUGIN_INSTANCE,
            scope_id=context.plugin_instance_id, installation_scope=anchors,
            plugin_instance_scope=instance_scope, artifact_digest=reference.artifact_digest,
            subject_id=context.subject_id, session_id=context.session_id,
        )
        runtime_context = TrustedRuntimeContext(
            resolved_tenant_id=tenant_id, subject_id=context.subject_id,
            session_id=context.session_id, authenticated=True,
            correlation_id=context.correlation_id,
        )
        query = InstanceResolveQuery(
            identity=identity, runtime_generation=context.runtime_generation,
            writer_generation=context.writer_generation,
        )
        try:
            snapshot = await self._m05.resolve(context=runtime_context, query=query)
        except Exception as exc:
            raise IdentityResolutionError("m05_exact_instance_resolution_failed") from exc
        if (snapshot is None or snapshot.identity != identity
                or snapshot.lifecycle is not InstanceLifecycleState.PUBLISHED
                or snapshot.availability is not RuntimeAvailability.ACTIVE
                or snapshot.runtime_generation != context.runtime_generation
                or snapshot.writer_generation != context.writer_generation):
            raise IdentityResolutionError("m05_instance_missing_ambiguous_or_stale")
        return ResolvedGrantIdentity(
            tenant_id=tenant_id, company_id=context.company_id,
            app_definition_id=reference.app_definition_id, app_id=reference.app_id,
            app_version_id=reference.app_version_id, manifest_version=reference.manifest_version,
            artifact_digest=reference.artifact_digest, scope_kind=ScopeKind.PLUGIN_INSTANCE.value,
            scope_id=context.plugin_instance_id, installation_scope_kind=anchors.scope_kind.value,
            installation_scope_id=anchors.scope_id, cabinet_id=anchors.cabinet_id,
            project_id=anchors.project_id, subject_kind=context.subject_kind,
            subject_id=context.subject_id, session_id=context.session_id,
            plugin_instance_id=context.plugin_instance_id,
            runtime_generation=snapshot.runtime_generation, writer_generation=snapshot.writer_generation,
            transaction_id=context.transaction_id, correlation_id=context.correlation_id,
            rls_evidence_digest=rls_digest,
            owner_evidence_digest=scope_digest, definition_evidence_digest=definition_digest,
            instance_evidence_digest=digest({
                "identity_digest": identity.digest,
                "runtime_generation": snapshot.runtime_generation,
                "writer_generation": snapshot.writer_generation,
                "transaction_id": context.transaction_id,
                "correlation_id": context.correlation_id,
            }), requested_capabilities=requested,
        )


def _handoff_matches(gates: DependencyGates, context: AuthenticatedGrantContext,
                     source: LegacyGrantSource) -> bool:
    return bool(
        gates.tenant_id == context.tenant_id
        and gates.app_definition_id == source.app_definition_id
        and gates.app_definition_id == context.app_definition_id
        and gates.app_version_id == source.app_version_id
        and gates.scope_kind == "plugin_instance" and gates.scope_id == context.plugin_instance_id
        and gates.installation_scope_kind == source.asserted_scope_kind
        and gates.installation_scope_id == source.asserted_scope_id
        and gates.plugin_instance_id == context.plugin_instance_id
        and gates.subject_id == context.subject_id and gates.session_id == context.session_id
        and gates.runtime_generation == context.runtime_generation
        and gates.writer_generation == context.writer_generation
        and gates.transaction_id == context.transaction_id
        and gates.correlation_id == context.correlation_id
    )


def _scope_anchors(context: AuthenticatedGrantContext, source: LegacyGrantSource,
                   tenant_id: str) -> ScopeAnchors:
    if source.asserted_scope_kind is None or source.asserted_scope_id is None:
        raise IdentityResolutionError("m04_scope_assertion_missing")
    try:
        kind = ScopeKind(source.asserted_scope_kind)
    except ValueError as exc:
        raise IdentityResolutionError("m04_scope_kind_unsupported") from exc
    if kind is ScopeKind.TENANT:
        expected = tenant_id
        cabinet_id, project_id = None, None
    elif kind is ScopeKind.CABINET:
        expected = context.cabinet_id
        cabinet_id, project_id = context.cabinet_id, None
    elif kind is ScopeKind.PROJECT:
        expected = context.project_id
        cabinet_id, project_id = context.cabinet_id, context.project_id
    else:
        raise IdentityResolutionError("m04_plugin_instance_scope_requires_m05")
    if source.asserted_scope_id != expected:
        raise IdentityResolutionError("m04_scope_context_mismatch")
    return ScopeAnchors(scope_kind=kind, scope_id=source.asserted_scope_id,
                        tenant_id=tenant_id, cabinet_id=cabinet_id, project_id=project_id)


def _json_value(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


__all__ = ["ApplicationIdentityResolutionAdapter"]
