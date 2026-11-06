"""MIG-RES-01 algorithms: import, profile selection, and read-only resolution."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from .lifecycle_contracts import (
    BindingState,
    CompatibilityCandidate,
    ImportCheckpoint,
    ImportOutcome,
    ImportRecord,
    ProfileCandidate,
    ProfileSelection,
    ProjectSelectionPolicy,
    ResourceImportContext,
    ResourceImportPage,
    ResourceImportPartition,
    ResourceKind,
    ResourceLifecycle,
    ResourceProfilePort,
    ResourceResolution,
    ResourceSnapshot,
    ResourceVersion,
    ScopeKind,
    TrustedResourceImportPort,
    TrustedResourceScopePort,
    TrustedResourceSnapshotPort,
    VersionStatus,
    canonical_digest,
)
from .lifecycle_validation import candidate_asset_digests, candidate_canonical_digest, validate_candidate, validate_resource_payload


def _profile_key(candidate: ProfileCandidate) -> tuple[object, ...]:
    return (
        candidate.profile_id,
        candidate.binding_id,
        candidate.resource_id,
        candidate.tenant_id,
        candidate.priority,
        candidate.version_number,
        candidate.project_ids or (),
        candidate.bound_project_ids or (),
        candidate.selection_policy.value if candidate.selection_policy else "",
        candidate.generation,
    )


def _profile_digest(candidates: Sequence[ProfileCandidate]) -> str:
    return canonical_digest([
        {
            "profile_id": item.profile_id,
            "resource_id": item.resource_id,
            "binding_id": item.binding_id,
            "tenant_id": item.tenant_id,
            "project_ids": item.project_ids,
            "bound_project_ids": item.bound_project_ids,
            "selection_policy": item.selection_policy.value if item.selection_policy else None,
            "priority": item.priority,
            "version_number": item.version_number,
            "active": item.active,
            "lifecycle": item.lifecycle.value,
            "generation": item.generation,
        }
        for item in sorted(candidates, key=_profile_key)
    ])


def select_deterministic_profile(
    candidates: Sequence[ProfileCandidate],
    *,
    tenant_id: str,
    project_id: str,
) -> ProfileSelection:
    """Resolve one profile using explicit binding policy and stable tie-breakers.

    Explicit project lists outrank the all-bound policy. Priority then wins;
    stable profile/binding/resource/version identity breaks every remaining tie.
    Active malformed candidates fail closed instead of causing a fallback.
    """
    ordered = tuple(sorted(candidates, key=_profile_key))
    digest = _profile_digest(ordered)
    if not tenant_id.strip() or not project_id.strip():
        return ProfileSelection(None, ordered, "selection_context_missing", digest)
    if any(item.tenant_id != tenant_id for item in ordered):
        return ProfileSelection(None, ordered, "cross_tenant_profile", digest)
    for item in ordered:
        if item.active and item.lifecycle is ResourceLifecycle.ACTIVE:
            if item.policy_error() is not None:
                return ProfileSelection(None, ordered, item.policy_error() or "profile_policy_invalid", digest)
    eligible = tuple(item for item in ordered if item.matches(project_id))
    if not eligible:
        return ProfileSelection(None, ordered, "profile_not_bound_to_project", digest)
    ranked = sorted(eligible, key=lambda item: (
        -item.specificity,
        -item.priority,
        item.profile_id,
        item.binding_id,
        item.resource_id,
        item.version_number,
    ))
    selected = ranked[0]
    rank = (
        selected.specificity,
        selected.priority,
        selected.profile_id,
        selected.binding_id,
        selected.resource_id,
        selected.version_number,
    )
    return ProfileSelection(selected, ordered, "resolved", digest, rank)


async def resolve_profile(
    *,
    port: ResourceProfilePort | None,
    tenant_id: str | None,
    project_id: str | None,
    session_id: str | None = None,
) -> ProfileSelection:
    """Read candidates from the server port and apply the pure selection policy."""
    if port is None or not tenant_id or not project_id:
        return ProfileSelection(None, (), "trusted_profile_port_or_context_missing", canonical_digest([]))
    candidates = tuple(await port.list_profile_candidates(
        tenant_id=tenant_id,
        project_id=project_id,
        session_id=session_id,
    ))
    return select_deterministic_profile(candidates, tenant_id=tenant_id, project_id=project_id)


async def _scope_reason(candidate: CompatibilityCandidate, context: ResourceImportContext) -> str | None:
    resource, binding = candidate.resource, candidate.binding
    if context.tenant_id and resource.scope.tenant_id != context.tenant_id:
        return "cross_tenant_resource"
    if binding.scope.tenant_id != resource.scope.tenant_id or binding.effective_target_scope.tenant_id != resource.scope.tenant_id:
        return "cross_tenant_binding"
    if not context.usable:
        return "scope_dependency_gate_missing"
    if context.scope_port is None or not hasattr(context.scope_port, "prove_import_scope"):
        return "scope_port_unavailable"
    port = context.scope_port
    try:
        proof = await getattr(port, "prove_import_scope")(
            tenant_id=context.tenant_id or "",
            owner_scope=resource.scope,
            target_scope=binding.effective_target_scope,
            project_ids=binding.project_ids,
            selection_policy=binding.selection_policy,
        )
    except (TypeError, ValueError):
        return "scope_proof_invalid"
    if proof is None:
        return "scope_proof_missing_or_ambiguous"
    target = binding.effective_target_scope
    if proof.tenant_id != context.tenant_id or proof.kind is not target.kind or proof.scope_id != target.scope_id or proof.target_scope != target:
        return "scope_proof_cross_tenant_or_mismatch"
    return None


async def _preflight(
    *,
    candidate: CompatibilityCandidate,
    dependencies_ready: bool,
    import_context: ResourceImportContext | None,
    expected_tenant_id: str | None = None,
) -> tuple[str, ImportOutcome | None, str | None]:
    digest = candidate_canonical_digest(candidate)
    if not candidate.digest or candidate.digest != digest:
        return digest, ImportOutcome.QUARANTINED, "canonical_digest_mismatch"
    if expected_tenant_id is not None and candidate.resource.scope.tenant_id != expected_tenant_id:
        return digest, ImportOutcome.QUARANTINED, "cross_tenant_partition"
    reasons = validate_candidate(candidate)
    if reasons:
        return digest, ImportOutcome.QUARANTINED, reasons[0]
    if import_context is not None:
        reason = await _scope_reason(candidate, import_context)
        if reason:
            blocked_reasons = {"scope_dependency_gate_missing", "scope_port_unavailable"}
            return digest, ImportOutcome.BLOCKED if reason in blocked_reasons else ImportOutcome.QUARANTINED, reason
    if not dependencies_ready:
        return digest, ImportOutcome.BLOCKED, "dependency_gate_missing"
    return digest, None, None


async def import_compatibility_candidate(
    *,
    store: TrustedResourceImportPort,
    candidate: CompatibilityCandidate,
    dependencies_ready: bool = False,
    import_context: ResourceImportContext | None = None,
) -> ImportRecord:
    """Classify one source row, then invoke exactly one trusted atomic port."""
    digest, outcome, reason = await _preflight(
        candidate=candidate,
        dependencies_ready=dependencies_ready,
        import_context=import_context,
    )
    if outcome is not None:
        return ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version, digest, outcome, reason, asset_digests=candidate_asset_digests(candidate))
    if not hasattr(store, "import_if_absent_or_record_conflict"):
        return ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version, digest, ImportOutcome.BLOCKED, "trusted_atomic_import_port_unavailable", asset_digests=candidate_asset_digests(candidate))
    result = await store.import_if_absent_or_record_conflict(
        identity=(candidate.source_kind, candidate.source_id, candidate.schema_version),
        digest=digest,
        candidate=candidate,
    )
    if isinstance(result, ImportRecord):
        if result.digest != digest or result.source_id != candidate.source_id:
            raise RuntimeError("trusted import returned mismatched source evidence")
        return result
    # Old migration fakes returned a small object. Normalize it without
    # weakening the new port's atomic identity/digest requirement.
    if getattr(result, "digest", digest) != digest:
        raise RuntimeError("trusted import returned mismatched source digest")
    return ImportRecord(
        candidate.source_kind,
        candidate.source_id,
        candidate.schema_version,
        digest,
        ImportOutcome(getattr(result, "outcome", ImportOutcome.BLOCKED)),
        getattr(result, "reason", "legacy_import_record"),
        asset_digests=candidate_asset_digests(candidate),
    )


class ResourceImportCoordinator:
    """Process sorted bounded pages; row evidence and checkpoints are port-owned."""

    def __init__(
        self,
        *,
        source: object,
        store: object,
        page_size: int = 100,
        dependencies_ready: bool = False,
        import_context: ResourceImportContext | None = None,
    ) -> None:
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        self._source, self._store = source, store
        self._page_size = page_size
        self._dependencies_ready = dependencies_ready
        self._import_context = import_context

    async def run_page(self, *, partition: ResourceImportPartition, correlation_id: str) -> ResourceImportPage:
        if not correlation_id.strip():
            return ResourceImportPage((), partition.initial_checkpoint(), False, "correlation_id_missing")
        if not hasattr(self._store, "load_checkpoint") or not hasattr(self._store, "import_row"):
            return ResourceImportPage((), partition.initial_checkpoint(), False, "resumable_import_port_unavailable")
        checkpoint = await getattr(self._store, "load_checkpoint")(partition=partition) or partition.initial_checkpoint()
        if not self._matches_partition(checkpoint, partition):
            return ResourceImportPage((), checkpoint, False, "checkpoint_partition_mismatch")
        if checkpoint.complete:
            return ResourceImportPage((), checkpoint, True, "already_complete")
        rows = tuple(sorted(await getattr(self._source, "read_page")(
            partition=partition,
            after_source_id=checkpoint.after_source_id,
            limit=self._page_size,
        ), key=lambda item: (item.source_id, item.schema_version, item.source_kind)))
        if len(rows) > self._page_size:
            return ResourceImportPage((), checkpoint, False, "source_page_exceeds_limit")
        if any(not row.source_id.strip() for row in rows):
            return ResourceImportPage((), checkpoint, False, "source_identity_missing")
        if any(rows[index].source_id == rows[index - 1].source_id for index in range(1, len(rows))):
            return ResourceImportPage((), checkpoint, False, "duplicate_source_identity_in_page")
        if checkpoint.after_source_id is not None and any(row.source_id <= checkpoint.after_source_id for row in rows):
            return ResourceImportPage((), checkpoint, False, "source_page_not_after_checkpoint")

        records: list[ImportRecord] = []
        current = checkpoint
        for candidate in rows:
            digest, outcome, reason = await _preflight(
                candidate=candidate,
                dependencies_ready=self._dependencies_ready,
                import_context=self._import_context,
                expected_tenant_id=partition.tenant_id,
            )
            next_checkpoint = replace(current, after_source_id=candidate.source_id, sequence=current.sequence + 1)
            if outcome is ImportOutcome.BLOCKED:
                return ResourceImportPage(tuple(records), current, False, reason)
            record = await getattr(self._store, "import_row")(
                identity=(candidate.source_kind, candidate.source_id, candidate.schema_version),
                digest=digest,
                candidate=candidate,
                checkpoint=next_checkpoint,
                quarantine_reason=reason if outcome is ImportOutcome.QUARANTINED else None,
                correlation_id=correlation_id,
            )
            if not isinstance(record, ImportRecord) or record.digest != digest or record.source_id != candidate.source_id:
                raise RuntimeError("resumable import returned mismatched evidence")
            returned_checkpoint = record.checkpoint or next_checkpoint
            if returned_checkpoint.sequence != next_checkpoint.sequence or returned_checkpoint.after_source_id != candidate.source_id:
                raise RuntimeError("resumable import returned a non-advancing checkpoint")
            if record.outcome is ImportOutcome.BLOCKED:
                return ResourceImportPage(tuple(records), current, False, record.reason)
            records.append(record)
            current = returned_checkpoint
            if not self._matches_partition(current, partition):
                raise RuntimeError("resumable import returned a foreign checkpoint")
        complete = len(rows) < self._page_size
        final = replace(current, complete=complete)
        if final != current:
            commit = getattr(self._store, "commit_checkpoint", None)
            if commit is None or not await commit(partition=partition, expected_sequence=current.sequence, checkpoint=final):
                return ResourceImportPage(tuple(records), current, False, "checkpoint_completion_conflict")
            current = final
        return ResourceImportPage(tuple(records), current, complete, None)

    @staticmethod
    def _matches_partition(checkpoint: ImportCheckpoint, partition: ResourceImportPartition) -> bool:
        return checkpoint.partition_id == partition.partition_id and checkpoint.tenant_id == partition.tenant_id and checkpoint.source_snapshot_id == partition.source_snapshot_id and checkpoint.source_schema_version == partition.source_schema_version and checkpoint.source_snapshot_digest == partition.source_snapshot_digest


async def resolve_resource(
    *,
    store: TrustedResourceSnapshotPort,
    scope_resolver: object | None,
    m04_scope_ready: bool,
    tenant_id: str | None,
    subject_id: str | None,
    session_id: str | None,
    project_id: str | None,
    binding_id: str,
    resource_id: str,
    version_id: str,
    profile_id: str | None = None,
) -> ResourceResolution:
    """Read one exact server snapshot; no independent reads or side effects."""
    if not m04_scope_ready or scope_resolver is None:
        return ResourceResolution(None, "m04_scope_resolver_unavailable")
    if not all(value and value.strip() for value in (tenant_id, subject_id, binding_id, resource_id, version_id)):
        return ResourceResolution(None, "authenticated_scope_context_missing")
    if not hasattr(store, "resolve_snapshot"):
        return ResourceResolution(None, "trusted_snapshot_port_unavailable")
    if hasattr(scope_resolver, "resolve_scope"):
        proof = await scope_resolver.resolve_scope(tenant_id=tenant_id, subject_id=subject_id, session_id=session_id, project_id=project_id)
        if proof is None or proof is False:
            return ResourceResolution(None, "scope_proof_denied")
    elif hasattr(scope_resolver, "is_authorized"):
        allowed = await scope_resolver.is_authorized(tenant_id=tenant_id, subject_id=subject_id, session_id=session_id, project_id=project_id)
        if allowed is not True:
            return ResourceResolution(None, "scope_proof_denied")
    else:
        return ResourceResolution(None, "scope_proof_port_unavailable")

    kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "subject_id": subject_id,
        "session_id": session_id,
        "project_id": project_id,
        "binding_id": binding_id,
        "resource_id": resource_id,
        "version_id": version_id,
    }
    if profile_id is not None:
        kwargs["profile_id"] = profile_id
    snapshot = await store.resolve_snapshot(**kwargs)
    if snapshot is None:
        return ResourceResolution(None, "immutable_snapshot_unavailable_or_denied")

    target = snapshot.binding.effective_target_scope
    mismatch = (
        snapshot.binding.binding_id != binding_id
        or snapshot.resource.resource_id != resource_id
        or snapshot.version.version_id != version_id
        or snapshot.scope_proof.tenant_id != tenant_id
        or snapshot.scope_proof.subject_id != subject_id
        or snapshot.scope_proof.session_id != session_id
        or snapshot.scope_proof.project_id != project_id
        or snapshot.binding.scope.tenant_id != tenant_id
        or snapshot.resource.scope.tenant_id != tenant_id
        or snapshot.binding.policy_errors()
        or snapshot.binding.state is not BindingState.ACTIVE
        or snapshot.resource.lifecycle is not ResourceLifecycle.ACTIVE
        or snapshot.version.status is not VersionStatus.PUBLISHED
        or (snapshot.resource.kind in {ResourceKind.PROMPT, ResourceKind.FILE, ResourceKind.MCP} and snapshot.resource.active_version != snapshot.version.sequence)
        or snapshot.binding.version_id != snapshot.version.version_id
        or (target.kind is ScopeKind.PROJECT and target.scope_id != project_id)
        or (target.kind is ScopeKind.SESSION and target.scope_id != session_id)
        or (profile_id is not None and snapshot.resource.profile_id != profile_id and snapshot.binding.profile_id != profile_id)
        or validate_resource_payload(snapshot.resource.kind, snapshot.version.payload)
    )
    if mismatch:
        return ResourceResolution(None, "immutable_snapshot_identity_or_lifecycle_mismatch")
    return ResourceResolution(snapshot, "resolved", provenance=snapshot.provenance)


__all__ = [
    "ResourceImportCoordinator",
    "import_compatibility_candidate",
    "resolve_profile",
    "resolve_resource",
    "select_deterministic_profile",
]
