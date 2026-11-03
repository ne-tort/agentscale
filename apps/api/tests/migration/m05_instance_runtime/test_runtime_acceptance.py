"""MIG-M05-02 detailed acceptance contracts; intentionally not executed during freeze."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from prodavan.application.dynamic_apps_definition.contracts import (
    DefinitionResolution,
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionRef,
)
from prodavan.domain.dynamic_apps.types import AppDefinition, AppScope, AppScopeKind, AppVersion
from prodavan.application.migration.m04_scope.scope_migration import (
    ScopeResolution,
    ScopeResolutionResult,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    BackfillRecord,
    EvidenceStatus,
    HandoffGate as M04EHandoffGate,
    HandoffStatus as M04EHandoffStatus,
    M05EvidenceExport,
    M05HandoffMarker,
    export_m05_evidence,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    FailClosedResolvePluginInstance, FinalDeltaEvidence, HandoffStatus, ImportCheckpoint,
    ImportOutcome, InstanceBindingEvidence, InstanceIdentity, InstanceImportCandidate,
    InstanceImportRecord, InstanceImportService, InstanceLifecycleService,
    InstanceLifecycleState, InstanceResolveQuery, InstanceSourceRuntime,
    LegacyWriterFenceEvidence, M04ScopeGateEvidence, M05RuntimeGateEvidence,
    M05UpstreamEvidence, ParityEvidence, QuarantineRetryAttempt, QuarantineRetryOutcome,
    QuarantineRetryState, RuntimeAuditRecord, RuntimeAvailability, RuntimeSnapshot,
    SOURCE_ID_MAX_LENGTH,
    SubjectSessionBindingEvidence, TrustedRuntimeContext, WriterCasResult, advance_runtime_generation,
    advance_writer_generation, build_m06_handoff_record, InstanceQuarantineRetryService,
)
from prodavan.application.migration.m05_instance_runtime import runtime_service as runtime_service_module
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ParentCompatibilityProof,
    ScopeAnchors,
    ScopeKind,
    ScopeProvenance,
    ScopeRegistryEntry,
)

D = "sha256:" + "a" * 64
SNAPSHOT_ID = "snapshot-1"
PARTITION = ("tenant-1", "definition-1")
CORRELATION = "opaque-correlation"


@pytest.fixture(autouse=True)
def contract_ready_upstream_gate(monkeypatch):
    monkeypatch.setattr(runtime_service_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)


def scope_anchors(kind=ScopeKind.CABINET):
    if kind is ScopeKind.TENANT:
        return ScopeAnchors(scope_kind=kind, scope_id="tenant-1", tenant_id="tenant-1")
    return ScopeAnchors(scope_kind=kind, scope_id="cabinet-1", tenant_id="tenant-1", cabinet_id="cabinet-1")


def identity(**changes):
    instance_id = changes.get("plugin_instance_id", "instance-1")
    tenant_id = changes.get("tenant_id", "tenant-1")
    installation = changes.get("installation_scope", scope_anchors())
    plugin_scope = changes.get("plugin_instance_scope") or ScopeAnchors(
        scope_kind=ScopeKind.PLUGIN_INSTANCE, scope_id=instance_id, tenant_id=tenant_id,
        cabinet_id=installation.cabinet_id, project_id=installation.project_id,
        plugin_instance_id=instance_id)
    base = InstanceIdentity(
        plugin_instance_id="instance-1", definition_id="definition-1", version_id="version-1",
        app_id="app-1", manifest_version="1.0.0", tenant_id="tenant-1",
        scope_kind=ScopeKind.PLUGIN_INSTANCE, scope_id="instance-1",
        installation_scope=scope_anchors(), plugin_instance_scope=plugin_scope,
        artifact_digest=D, subject_id="subject-1", session_id="session-1",
    )
    defaults = {"plugin_instance_id": instance_id, "tenant_id": tenant_id,
                "scope_id": instance_id, "installation_scope": installation,
                "plugin_instance_scope": plugin_scope}
    return replace(base, **(defaults | changes))


def checkpoint(after=None, sequence=0):
    return ImportCheckpoint(PARTITION, SNAPSHOT_ID, "schema-v1", D, after, sequence)


def source_id_of_length(length):
    prefix = f"legacy-source-{length}-"
    return prefix + ("x" * (length - len(prefix)))


def upstream(*, m04_ready=True, m04e_ready=True, m05_schema_ready=True,
              m05_rows_ready=True, m04_partition=PARTITION, export_partition=PARTITION,
              m04_digest=D, export_digest=D, m05_digest=D):
    m04 = M04ScopeGateEvidence(
        m04_partition, D, m04_digest, D, D, D, (D,), 1 if m04_ready else 0,
        0 if m04_ready else 1, 0 if m04_ready else 1,
    )
    m04e_checkpoint = BackfillCheckpoint(
        export_partition, "version", "version-1", 2, "m04e-snapshot", "m04e-schema",
        export_digest, "m04e.v1", 9, True,
    )
    definition_checkpoint = replace(
        m04e_checkpoint, phase="definition", after_source_id="definition-1", sequence=1,
        phase_complete=False,
    )
    version_checkpoint = replace(
        m04e_checkpoint, after_source_id="version-1", phase_complete=False,
    )
    definition_row = BackfillRecord(
        "legacy-definition", "definition-1", "m04e-schema", D, D, ("tenant-1", "app-1"),
        definition_checkpoint, EvidenceStatus.ACCEPTED, None, CORRELATION,
        tenant_id="tenant-1", app_definition_id="definition-1", phase="definition",
        source_snapshot_digest=export_digest,
        proof_evidence=(("owner", D), ("active_scope", D)), transaction_id="m04e-tx",
        writer_generation=9,
    )
    version_row = BackfillRecord(
        "legacy-version", "version-1", "m04e-schema", D, D,
        ("tenant-1", "definition-1", "1.0.0"), version_checkpoint,
        EvidenceStatus.ACCEPTED, None, CORRELATION, tenant_id="tenant-1",
        app_definition_id="definition-1", phase="version", source_snapshot_digest=export_digest,
        proof_evidence=(("owner", D), ("active_scope", D), ("same_definition", D),
                        ("immutable_artifact", D)), artifact_digest=D,
        transaction_id="m04e-tx", writer_generation=9,
    )
    gates = (
        M04EHandoffGate("MIG-M04-01", m04_ready, D, None if m04_ready else "blocked"),
        M04EHandoffGate("MIG-M04-02", m04_ready, m04_digest, None if m04_ready else "blocked"),
        M04EHandoffGate("MIG-M04E-01", m04e_ready, D, None if m04e_ready else "blocked"),
        M04EHandoffGate("active_typed_parent_scope", m04_ready and m04e_ready, D,
                        None if m04_ready and m04e_ready else "blocked"),
        M04EHandoffGate("same_definition_version", m04e_ready, D, None if m04e_ready else "blocked"),
        M04EHandoffGate("immutable_artifact", m04e_ready, D, None if m04e_ready else "blocked"),
    )
    export_rows = (definition_row, version_row) if m04e_ready else ()
    export = export_m05_evidence(
        tenant_id=export_partition[0], app_definition_id=export_partition[1],
        records=export_rows, source_snapshot_digest=export_digest,
        checkpoint=m04e_checkpoint, gates=gates,
    )
    gates = tuple(
        replace(gate, evidence_digest=export.canonical_digest)
        if gate.name == "MIG-M04E-01" and m04e_ready else gate
        for gate in gates
    )
    export = replace(export, gates=gates,
                     status=M04EHandoffStatus.READY if m04e_ready else M04EHandoffStatus.BLOCKED)
    if m04e_ready:
        export = replace(export, handoff_marker=M05HandoffMarker(
            "handoff-1", "MIG-M04E-02", M04EHandoffStatus.READY,
            export_partition, export_digest, export.canonical_digest, m04e_checkpoint,
            len(export.accepted), len(export.quarantined), gates,
            "actor", "recorded-at", "decision-owner", m04e_checkpoint.writer_generation,
            CORRELATION, False,
        ))
    m05 = M05RuntimeGateEvidence(
        partition=PARTITION, source_snapshot_digest=m05_digest, canonical_digest=D,
        checkpoint_digest=D, m04_scope_digest=m04_digest,
        m04e_export_digest=export.canonical_digest,
        exact_instance_key_digest=D, registry_to_instance_fk_digest=D,
        instance_to_registry_fk_digest=D, artifact_validation_digest=D, rls_review_digest=D,
        writer_cas_review_digest=D, runtime_cas_review_digest=D,
        stale_worker_denial_digest=D, audit_atomicity_digest=D,
        unresolved_required_instances=0 if m05_rows_ready else 1,
    )
    if not m05_schema_ready:
        m05 = replace(m05, rls_review_digest="missing")
    return M05UpstreamEvidence(m04, export, m05)


def context(**changes):
    return replace(TrustedRuntimeContext("tenant-1", "subject-1", "session-1", True, CORRELATION), **changes)


def active_entry(anchors=None, state=OwnerState.ACTIVE):
    anchors = anchors or scope_anchors()
    return ScopeRegistryEntry(
        anchors=anchors, owner_state=state,
        provenance=ScopeProvenance(source_kind="legacy", source_id="scope-1", schema_version="v1", payload_digest=D),
    )


def resolved_definition(item=None):
    item = item or identity()
    ref = DefinitionVersionRef("tenant-1", "app-1", item.definition_id, item.version_id, "1.0.0", item.artifact_digest)
    now = datetime.now(timezone.utc)
    definition = AppDefinition(
        id=item.definition_id, tenant_id=item.tenant_id, app_id="app-1", display_name="App",
        scope=AppScope(kind=AppScopeKind.TENANT, id="tenant-1"), created_at=now, updated_at=now)
    version = AppVersion(
        id=item.version_id, app_definition_id=item.definition_id, manifest_version="1.0.0",
        artifact_digest=item.artifact_digest,
        manifest={"protocol": "1.0", "appId": "app-one", "displayName": "App", "version": "1.0.0",
                  "requestedCapabilities": [], "entrypoints": {"plugin": {"artifact": {
                      "sha256": item.artifact_digest.removeprefix("sha256:")}}}},
        status=definition.status, created_at=now)
    return DefinitionResolution(
        DefinitionResolutionOutcome.RESOLVED, reference=ref, definition=definition, version=version,
        evidence={"correlation_id": CORRELATION},
    )


class Reader:
    def __init__(self, rows, *, force_duplicate=False):
        self.rows = list(rows)
        self.force_duplicate = force_duplicate
        self.calls = []

    async def read_page(self, *, partition, source_snapshot_id, source_schema_version,
                        source_snapshot_digest, after_source_id, limit):
        self.calls.append((partition, source_snapshot_id, source_schema_version,
                           source_snapshot_digest, after_source_id, limit))
        selected = sorted(
            (r for r in self.rows if after_source_id is None or r.source_id > after_source_id),
            key=lambda row: row.source_id,
        )[:limit]
        return [*selected, *selected] if self.force_duplicate else selected


class Audit:
    def __init__(self, fail=False):
        self.records = []
        self.fail = fail

    async def append(self, record):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.records.append(record)


class Evidence:
    def __init__(self, *, scope=None, plugin_scope=None, definition=None):
        self.scope = scope or ScopeResolutionResult(ScopeResolution.ACTIVE, active_entry(), "active", CORRELATION)
        expected_plugin_scope = ScopeAnchors(scope_kind=ScopeKind.PLUGIN_INSTANCE, scope_id="instance-1",
                                             tenant_id="tenant-1", cabinet_id="cabinet-1",
                                             plugin_instance_id="instance-1")
        self.plugin_scope = plugin_scope or ScopeResolutionResult(
            ScopeResolution.ACTIVE, active_entry(expected_plugin_scope), "active", CORRELATION)
        self.definition = definition
        self.scope_calls = 0
        self.plugin_scope_calls = 0
        self.definition_calls = 0
        self.session_calls = 0

    async def resolve_installation_scope(self, *, context, anchors):
        self.scope_calls += 1
        if self.scope.entry is not None and self.scope.entry.anchors != anchors:
            return ScopeResolutionResult(ScopeResolution.DENY, None, "anchor_mismatch", CORRELATION)
        return replace(self.scope, correlation_id=context.correlation_id)

    async def resolve_plugin_instance_scope(self, *, context, identity):
        self.plugin_scope_calls += 1
        expected = self.plugin_scope.entry.anchors if self.plugin_scope.entry else None
        if expected is None or expected.plugin_instance_id != identity.plugin_instance_id:
            return ScopeResolutionResult(ScopeResolution.DENY, None, "instance_scope_mismatch", CORRELATION)
        return replace(self.plugin_scope, correlation_id=context.correlation_id)

    async def prove_subject_session(self, *, context, identity):
        self.session_calls += 1
        if (identity.tenant_id, identity.subject_id, identity.session_id) != (
            "tenant-1", "subject-1", "session-1"
        ):
            return None
        return SubjectSessionBindingEvidence(
            tenant_id=identity.tenant_id, subject_id=identity.subject_id,
            session_id=identity.session_id, session_tenant_id=identity.tenant_id,
            session_subject_id=identity.subject_id, subject_active=True, session_active=True,
            correlation_id=context.correlation_id, evidence_digest=D,
        )

    async def resolve_definition_version(self, *, context, tenant_id, app_id, manifest_version,
                                         expected_definition_id, expected_version_id, artifact_digest):
        self.definition_calls += 1
        item = identity()
        self.last_definition_query = (tenant_id, app_id, manifest_version,
                                     expected_definition_id, expected_version_id, artifact_digest)
        if self.last_definition_query != (item.tenant_id, item.app_id, item.manifest_version,
                                          item.definition_id, item.version_id, item.artifact_digest):
            return DefinitionResolution(DefinitionResolutionOutcome.CONFLICT,
                                       reason=DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT)
        result = self.definition or resolved_definition(item)
        return replace(result, evidence={"correlation_id": context.correlation_id})


class Store:
    """Acceptance fake models cursor CAS plus atomic mapping/audit rollback semantics."""
    def __init__(self, *, prior=None, cursor=None, fail_audit=False):
        self.rows = {}
        if prior:
            self.rows[(prior.checkpoint.partition, prior.source_kind, prior.source_id, prior.schema_version)] = prior
        self.cursor = cursor
        self.commits = []
        self.fail_audit = fail_audit

    async def find(self, *, partition, source_kind, source_id, schema_version):
        return self.rows.get((partition, source_kind, source_id, schema_version))

    async def load_checkpoint(self, *, partition, source_snapshot_id, source_schema_version, source_snapshot_digest):
        return self.cursor

    async def find_instance(self, *, partition, plugin_instance_id):
        for record in self.rows.values():
            evidence = record.binding_evidence
            if (record.checkpoint.partition == partition and evidence is not None
                and evidence.identity.plugin_instance_id == plugin_instance_id):
                return record
        return None

    async def commit_record_and_checkpoint(self, *, context, expected_checkpoint, next_checkpoint, record, audit_record):
        if self.fail_audit:
            raise RuntimeError("audit append failed")
        if self.cursor != expected_checkpoint:
            raise RuntimeError("checkpoint CAS conflict")
        key = (record.checkpoint.partition, record.source_kind, record.source_id, record.schema_version)
        prior = self.rows.get(key)
        if prior is None:
            self.rows[key] = record
        elif prior.source_digest != record.source_digest:
            conflict_key = (*key, record.source_digest)
            self.rows[conflict_key] = record
        self.cursor = next_checkpoint
        self.commits.append((expected_checkpoint, next_checkpoint, record, audit_record))


class Lifecycle:
    def __init__(self, result: WriterCasResult | None = None):
        self.result = result
        self.calls = []

    async def resolve_exact(self, *, query: InstanceResolveQuery, context: TrustedRuntimeContext) -> RuntimeSnapshot | None:
        self.calls.append(("resolve", query, context))
        return self.result.snapshot if self.result else None

    async def compare_and_set_writer(self, *, identity: InstanceIdentity, context: TrustedRuntimeContext,
                                     expected_runtime_generation: int, expected_writer_generation: int,
                                     next_writer_generation: int, audit_record: RuntimeAuditRecord) -> WriterCasResult:
        self.calls.append(("writer", {"identity": identity, "context": context,
            "expected_runtime_generation": expected_runtime_generation,
            "expected_writer_generation": expected_writer_generation,
            "next_writer_generation": next_writer_generation, "audit_record": audit_record}))
        assert self.result is not None
        return self.result

    async def compare_and_set_runtime(self, *, identity: InstanceIdentity, context: TrustedRuntimeContext,
                                      expected_runtime_generation: int, expected_writer_generation: int,
                                      next_runtime_generation: int, next_writer_generation: int,
                                      audit_record: RuntimeAuditRecord) -> WriterCasResult:
        self.calls.append(("runtime", {"identity": identity, "context": context,
            "expected_runtime_generation": expected_runtime_generation,
            "expected_writer_generation": expected_writer_generation,
            "next_runtime_generation": next_runtime_generation,
            "next_writer_generation": next_writer_generation, "audit_record": audit_record}))
        assert self.result is not None
        return self.result

    async def compare_and_set_lifecycle(self, *, identity: InstanceIdentity, context: TrustedRuntimeContext,
                                        expected_lifecycle: InstanceLifecycleState,
                                        next_lifecycle: InstanceLifecycleState,
                                        expected_runtime_generation: int, expected_writer_generation: int,
                                        next_writer_generation: int,
                                        audit_record: RuntimeAuditRecord) -> WriterCasResult:
        self.calls.append(("lifecycle", {"identity": identity, "context": context,
            "expected_lifecycle": expected_lifecycle, "next_lifecycle": next_lifecycle,
            "expected_runtime_generation": expected_runtime_generation,
            "expected_writer_generation": expected_writer_generation,
            "next_writer_generation": next_writer_generation, "audit_record": audit_record}))
        assert self.result is not None
        return self.result


def candidate(source_id="src-1", *, item=None, runtime=None, payload=None):
    return InstanceImportCandidate(
        "legacy-instance", source_id, "schema-v1", payload or {"state": "published"},
        item or identity(), runtime or InstanceSourceRuntime(InstanceLifecycleState.PUBLISHED, 4, 7),
    )


def imported_record(source_id="src-1", *, item=None, source_checkpoint=None, source_digest=None):
    item = item or identity()
    source_checkpoint = source_checkpoint or checkpoint(source_id, 1)
    binding = InstanceBindingEvidence(
        item, InstanceSourceRuntime(InstanceLifecycleState.PUBLISHED, 4, 7),
        active_entry(item.installation_scope), active_entry(item.plugin_instance_scope),
        resolved_definition(item),
        SubjectSessionBindingEvidence(
            tenant_id=item.tenant_id, subject_id=item.subject_id, session_id=item.session_id,
            session_tenant_id=item.tenant_id, session_subject_id=item.subject_id,
            subject_active=True, session_active=True, correlation_id=CORRELATION,
            evidence_digest=D,
        ),
    )
    return InstanceImportRecord("legacy-instance", source_id, "schema-v1",
                                source_digest or candidate(source_id).digest, source_checkpoint,
                                ImportOutcome.IMPORTED, None, CORRELATION, binding,
                                checkpoint(source_id, source_checkpoint.sequence))


@pytest.mark.asyncio
async def test_incomplete_m04_or_m04e_or_m05_gate_blocks_before_candidate_read():
    for gates in (upstream(m04_ready=False), upstream(m04e_ready=False), upstream(m05_schema_ready=False),
                  upstream(m05_digest="sha256:" + "b" * 64)):
        reader, store, audit = Reader([candidate()]), Store(), Audit()
        result = await InstanceImportService(reader, store, Evidence(), audit, upstream=gates).run_page(
            checkpoint(), context=context())
        assert result.blocked_reason == "upstream_or_m05_gate_incomplete"
        assert reader.calls == [] and store.commits == []
        assert audit.records[-1].outcome == "blocked"


def test_missing_or_mismatched_m04e_marker_blocks_upstream_readiness():
    evidence = upstream()
    marker = evidence.m04e_export.handoff_marker
    assert marker is not None
    no_marker = replace(evidence.m04e_export, handoff_marker=None)
    wrong_marker = replace(marker, canonical_digest="sha256:" + "b" * 64)
    assert not replace(evidence, m04e_export=no_marker).ready_for_import(PARTITION, D)
    assert not replace(evidence, m04e_export=replace(evidence.m04e_export,
                                                       handoff_marker=wrong_marker)).ready_for_import(PARTITION, D)


def test_unconfirmed_m04_m04e_release_flag_blocks_positive_readiness(monkeypatch):
    monkeypatch.setattr(runtime_service_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", False)
    assert not upstream().ready_for_import(PARTITION, D)
    assert not upstream().ready_for_positive_transition(PARTITION)


def handoff_evidence(*, current_checkpoint=None, expected_count=1, imported_count=1,
                     writer_generation=9, unknown=0, unresolved=0, mismatches=0,
                     active_workers_remaining=0):
    current_checkpoint = current_checkpoint or checkpoint("src-1", 1)
    delta = FinalDeltaEvidence(PARTITION, SNAPSHOT_ID, D, current_checkpoint, expected_count, D)
    parity = ParityEvidence(PARTITION, current_checkpoint.digest, expected_count, imported_count,
                            unknown, unresolved, mismatches, D)
    fence = LegacyWriterFenceEvidence(PARTITION, writer_generation, active_workers_remaining, D)
    return delta, parity, fence


@pytest.mark.asyncio
async def test_import_exact_binding_uses_m04_scope_and_m04e_artifact_proofs():
    reader, store, audit, evidence = Reader([candidate()]), Store(), Audit(), Evidence()
    result = await InstanceImportService(reader, store, evidence, audit, upstream=upstream()).run_page(
        checkpoint(), context=context())
    assert result.records[0].outcome is ImportOutcome.IMPORTED
    assert result.records[0].binding_evidence.identity == identity()
    assert evidence.scope_calls == evidence.plugin_scope_calls == evidence.definition_calls == 1
    assert evidence.last_definition_query == (
        "tenant-1", "app-1", "1.0.0", "definition-1", "version-1", D)
    assert store.cursor.after_source_id == "src-1"
    assert store.commits[0][3].correlation_id == CORRELATION


@pytest.mark.asyncio
async def test_import_quarantines_artifact_or_parent_scope_mismatch():
    wrong_base = resolved_definition()
    assert wrong_base.reference is not None
    wrong_ref = replace(wrong_base, reference=replace(wrong_base.reference, artifact_digest="sha256:" + "b" * 64))
    bad_scope = ScopeResolutionResult(ScopeResolution.DENY, None, "archived", CORRELATION)
    for evidence, reason in ((Evidence(definition=wrong_ref), "definition_version_or_artifact_unproven"),
                             (Evidence(scope=bad_scope), "active_installation_scope_unproven")):
        store = Store()
        result = await InstanceImportService(Reader([candidate()]), store, evidence, Audit(),
                                             upstream=upstream()).run_page(checkpoint(), context=context())
        assert result.records[0].outcome is ImportOutcome.QUARANTINED
        assert result.records[0].reason_code == reason
        assert result.records[0].binding_evidence is None


@pytest.mark.asyncio
async def test_duplicate_or_over_limit_candidate_page_rejects_without_commit():
    duplicate_reader = Reader([candidate()], force_duplicate=True)
    duplicate_store = Store()
    with pytest.raises(ValueError, match="duplicate source identities"):
        await InstanceImportService(duplicate_reader, duplicate_store, Evidence(), Audit(),
                                    upstream=upstream()).run_page(checkpoint(), context=context())
    assert duplicate_store.commits == [] and duplicate_store.cursor is None


@pytest.mark.parametrize("length", [40, 41, 80])
def test_source_id_width_boundaries_preserve_exact_value_in_candidate_and_checkpoint(length):
    source_id = source_id_of_length(length)
    row = candidate(source_id)
    cursor = checkpoint(source_id, 1)
    assert len(source_id) == length
    assert row.source_id == source_id
    assert row.digest == candidate(source_id).digest
    assert cursor.after_source_id == source_id
    assert cursor.advance(source_id).after_source_id == source_id


def test_source_id_above_registered_orm_width_fails_closed_in_candidate_and_cursor():
    source_id = source_id_of_length(SOURCE_ID_MAX_LENGTH + 1)
    with pytest.raises(ValueError, match="registered ORM column width"):
        candidate(source_id)
    with pytest.raises(ValueError, match="registered ORM source_id width"):
        checkpoint(source_id, 1)


@pytest.mark.asyncio
async def test_max_width_source_id_is_ordered_replayed_and_checkpointed_verbatim():
    source_id = source_id_of_length(SOURCE_ID_MAX_LENGTH)
    row = candidate(source_id)
    prior = imported_record(source_id, source_checkpoint=checkpoint(source_id, 1))
    store = Store(prior=prior)
    result = await InstanceImportService(
        Reader([row]), store, Evidence(), Audit(), upstream=upstream()
    ).run_page(checkpoint(), context=context())
    assert result.records[0].outcome is ImportOutcome.REPLAY
    assert result.records[0].source_id == source_id
    assert result.records[0].checkpoint.after_source_id == source_id
    assert result.records[0].cursor_checkpoint == checkpoint(source_id, 1)
    assert result.checkpoint.after_source_id == source_id
    assert store.cursor is not None
    assert store.cursor.after_source_id == source_id


def test_max_width_source_id_is_retained_in_serialized_m06_evidence():
    source_id = source_id_of_length(SOURCE_ID_MAX_LENGTH)
    final_checkpoint = checkpoint(source_id, 1)
    row = imported_record(source_id, source_checkpoint=final_checkpoint)
    delta, parity, fence = handoff_evidence(current_checkpoint=final_checkpoint)
    result = build_m06_handoff_record(
        record_id="promotion-max-source-id", records=(row,), checkpoint=final_checkpoint,
        upstream=upstream(), actor="agent", recorded_at="fixed-time",
        decision_owner="coordinator", writer_generation=9, correlation_id=CORRELATION,
        final_delta=delta, parity=parity, legacy_fence=fence,
    )
    assert result.status is HandoffStatus.READY
    assert result.checkpoint.after_source_id == source_id
    assert result.canonical_positive_writes is False
    assert result.canonical_digest


@pytest.mark.asyncio
async def test_long_source_ids_are_sorted_before_duplicate_identity_and_cursor_advance():
    shorter = source_id_of_length(41)
    maximum = source_id_of_length(SOURCE_ID_MAX_LENGTH)
    result = await InstanceImportService(
        Reader([candidate(maximum), candidate(shorter)]), Store(), Evidence(), Audit(),
        upstream=upstream(), page_size=2,
    ).run_page(checkpoint(), context=context())
    assert [record.source_id for record in result.records] == [shorter, maximum]
    assert result.records[0].outcome is ImportOutcome.IMPORTED
    assert result.records[1].outcome is ImportOutcome.QUARANTINED
    assert result.records[1].reason_code == "duplicate_plugin_instance_identity"
    assert result.checkpoint.after_source_id == maximum


@pytest.mark.asyncio
async def test_quarantine_retry_above_registered_source_id_width_is_blocked_before_lookup():
    audit = Audit()
    store = RetryStore(None)
    result = await InstanceQuarantineRetryService(
        ExactCandidateReader(candidate()), store, Evidence(), audit, upstream=upstream()
    ).retry(partition=PARTITION, source_snapshot_id=SNAPSHOT_ID,
            source_schema_version="schema-v1", source_snapshot_digest=D,
            source_kind="legacy-instance", source_id=source_id_of_length(81),
            schema_version="schema-v1", context=context())
    assert result.outcome is QuarantineRetryOutcome.BLOCKED
    assert result.blocked_reason == "source_identity_invalid"
    assert store.calls == []
    assert audit.records[-1].reason_code == "source_identity_invalid"


@pytest.mark.asyncio
async def test_wrong_snapshot_cursor_does_not_read_source_page():
    reader, store = Reader([candidate()]), Store(cursor=checkpoint("src-1", 1))
    wrong = ImportCheckpoint(PARTITION, "snapshot-other", "schema-v1", D, "src-1", 1)
    result = await InstanceImportService(reader, store, Evidence(), Audit(), upstream=upstream()).run_page(
        wrong, context=context())
    assert result.blocked_reason == "checkpoint_snapshot_mismatch"
    assert reader.calls == [] and store.commits == []


@pytest.mark.asyncio
async def test_import_quarantines_missing_reciprocal_plugin_instance_scope_proof():
    wrong_anchors = ScopeAnchors(scope_kind=ScopeKind.PLUGIN_INSTANCE, scope_id="other-instance",
                                 tenant_id="tenant-1", plugin_instance_id="other-instance")
    evidence = Evidence(plugin_scope=ScopeResolutionResult(
        ScopeResolution.ACTIVE, active_entry(wrong_anchors), "wrong_instance", CORRELATION))
    result = await InstanceImportService(Reader([candidate()]), Store(), evidence, Audit(),
                                         upstream=upstream()).run_page(checkpoint(), context=context())
    assert result.records[0].outcome is ImportOutcome.QUARANTINED
    assert result.records[0].reason_code == "plugin_instance_registry_relation_unproven"
    assert result.records[0].binding_evidence is None
    assert evidence.definition_calls == 0


@pytest.mark.asyncio
async def test_import_quarantines_duplicate_exact_instance_across_source_rows():
    store = Store()
    result = await InstanceImportService(
        Reader([candidate("src-1"), candidate("src-2")]), store, Evidence(), Audit(), upstream=upstream()
    ).run_page(checkpoint(), context=context())
    assert [record.outcome for record in result.records] == [ImportOutcome.IMPORTED, ImportOutcome.QUARANTINED]
    assert result.records[1].reason_code == "duplicate_plugin_instance_identity"
    assert result.records[1].binding_evidence is None


@pytest.mark.asyncio
async def test_import_subject_session_and_tenant_are_server_bound():
    for call_context in (context(subject_id="other-subject"), context(session_id="other-session"),
                         context(resolved_tenant_id="tenant-other")):
        store = Store()
        start = checkpoint()
        if call_context.resolved_tenant_id != start.partition[0]:
            expected = "trusted_tenant_partition_mismatch"
        else:
            expected = None
        result = await InstanceImportService(Reader([candidate()]), store, Evidence(), Audit(),
                                             upstream=upstream()).run_page(start, context=call_context)
        if expected:
            assert result.blocked_reason == expected
        else:
            assert result.records[0].outcome is ImportOutcome.QUARANTINED
            assert result.records[0].reason_code == "authenticated_binding_mismatch"
            assert result.records[0].binding_evidence is None


@pytest.mark.asyncio
async def test_resumable_page_uses_durable_cursor_and_strictly_bounded_reader():
    initial = checkpoint()
    durable = checkpoint("src-1", 1)
    rows = [candidate("src-3"), candidate("src-2")]
    reader, store = Reader(rows), Store(cursor=durable)
    result = await InstanceImportService(reader, store, Evidence(), Audit(), upstream=upstream(), page_size=1).run_page(
        initial, context=context())
    assert reader.calls == [(PARTITION, SNAPSHOT_ID, "schema-v1", D, "src-1", 1)]
    assert len(result.records) == 1 and result.records[0].source_id == "src-2"
    assert result.checkpoint.after_source_id == "src-2" and result.checkpoint.sequence == 2


@pytest.mark.asyncio
async def test_stale_caller_checkpoint_ahead_of_durable_cursor_is_blocked():
    store = Store(cursor=checkpoint("src-1", 1))
    result = await InstanceImportService(Reader([candidate("src-2")]), store, Evidence(), Audit(),
                                         upstream=upstream()).run_page(checkpoint("src-2", 2), context=context())
    assert result.blocked_reason == "checkpoint_ahead_of_durable_cursor"
    assert store.commits == []


@pytest.mark.asyncio
async def test_exact_source_replay_preserves_first_mapping_and_advances_cursor_atomically():
    row = candidate()
    prior = imported_record("src-1", source_checkpoint=checkpoint("src-1", 1))
    store = Store(prior=prior)
    result = await InstanceImportService(Reader([row]), store, Evidence(), Audit(), upstream=upstream()).run_page(
        checkpoint(), context=context())
    assert result.records[0].outcome is ImportOutcome.REPLAY
    assert result.records[0].binding_evidence == prior.binding_evidence
    assert result.records[0].checkpoint == prior.checkpoint
    assert result.records[0].cursor_checkpoint == checkpoint("src-1", 1)
    assert store.cursor.after_source_id == "src-1"
    assert store.rows[(PARTITION, prior.source_kind, prior.source_id, prior.schema_version)] == prior


@pytest.mark.asyncio
async def test_changed_source_digest_is_conflict_and_does_not_overwrite_first_mapping():
    old = candidate(payload={"revision": 1})
    changed = candidate(payload={"revision": 2})
    prior = imported_record("src-1")
    store = Store(prior=prior)
    result = await InstanceImportService(Reader([changed]), store, Evidence(), Audit(), upstream=upstream()).run_page(
        checkpoint(), context=context())
    assert old.digest != changed.digest
    assert result.records[0].outcome is ImportOutcome.CONFLICT
    assert result.records[0].reason_code == "source_digest_changed"
    assert store.rows[(PARTITION, prior.source_kind, prior.source_id, prior.schema_version)] == prior
    assert len(store.rows) == 2


@pytest.mark.asyncio
async def test_audit_failure_aborts_mapping_and_checkpoint_commit():
    store = Store(fail_audit=True)
    service = InstanceImportService(Reader([candidate()]), store, Evidence(), Audit(), upstream=upstream())
    with pytest.raises(RuntimeError, match="audit append failed"):
        await service.run_page(checkpoint(), context=context())
    assert store.cursor is None and store.commits == [] and store.rows == {}


@pytest.mark.asyncio
async def test_resolver_denies_missing_gate_without_consulting_runtime_port():
    audit = Audit()
    lifecycle = Lifecycle(WriterCasResult(True, RuntimeSnapshot(identity(), InstanceLifecycleState.PUBLISHED,
        RuntimeAvailability.ACTIVE, 4, 7), "ok"))
    result = await FailClosedResolvePluginInstance(lifecycle, audit).resolve(
        context=context(), query=InstanceResolveQuery(identity(), 4, 7))
    assert result is None and lifecycle.calls == []
    assert audit.records[0].reason_code == "upstream_or_m05_gate_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [
    {"resolved_tenant_id": "tenant-2"}, {"subject_id": "subject-2"}, {"session_id": "session-2"},
])
async def test_resolver_denies_authenticated_binding_mismatch_without_lookup(mismatch):
    lifecycle, audit = Lifecycle(), Audit()
    result = await FailClosedResolvePluginInstance(lifecycle, audit, upstream=upstream(), identity_evidence=Evidence()).resolve(
        context=context(**mismatch), query=InstanceResolveQuery(identity(), 4, 7))
    assert result is None and lifecycle.calls == []
    assert audit.records[0].reason_code == "authenticated_binding_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(("snapshot", "reason"), [
    (None, "instance_missing_or_not_unique"),
    (RuntimeSnapshot(identity(), InstanceLifecycleState.PUBLISHED, RuntimeAvailability.ACTIVE, 3, 7), "stale_runtime_generation"),
    (RuntimeSnapshot(identity(), InstanceLifecycleState.PUBLISHED, RuntimeAvailability.ACTIVE, 4, 6), "stale_writer_generation"),
    (RuntimeSnapshot(identity(), InstanceLifecycleState.DISABLED, RuntimeAvailability.ACTIVE, 4, 7), "instance_lifecycle_not_published"),
    (RuntimeSnapshot(identity(), InstanceLifecycleState.PUBLISHED, RuntimeAvailability.ARCHIVED, 4, 7), "runtime_not_active"),
    (RuntimeSnapshot(identity(subject_id="other"), InstanceLifecycleState.PUBLISHED, RuntimeAvailability.ACTIVE, 4, 7), "exact_instance_binding_mismatch"),
])
async def test_resolver_rejects_stale_or_non_active_runtime_snapshots(snapshot, reason):
    lifecycle = Lifecycle(WriterCasResult(True, snapshot, "ok") if snapshot else None)
    audit = Audit()
    result = await FailClosedResolvePluginInstance(lifecycle, audit, upstream=upstream(), identity_evidence=Evidence()).resolve(
        context=context(), query=InstanceResolveQuery(identity(), 4, 7))
    assert result is None and audit.records[0].reason_code == reason


@pytest.mark.asyncio
async def test_exact_resolver_returns_only_full_matching_active_instance():
    snap = RuntimeSnapshot(identity(), InstanceLifecycleState.PUBLISHED, RuntimeAvailability.ACTIVE, 4, 7)
    audit = Audit()
    evidence = Evidence()
    result = await FailClosedResolvePluginInstance(Lifecycle(WriterCasResult(True, snap, "ok")), audit,
                                                   upstream=upstream(), identity_evidence=evidence).resolve(
        context=context(), query=InstanceResolveQuery(identity(), 4, 7))
    assert result == snap
    assert evidence.scope_calls == evidence.plugin_scope_calls == evidence.definition_calls == 1
    assert audit.records[0].outcome == "allowed"
    assert audit.records[0].runtime_generation == 4 and audit.records[0].writer_generation == 7
    assert not hasattr(audit.records[0], "subject_id")


@pytest.mark.asyncio
async def test_resolver_denies_wrong_reciprocal_plugin_scope_before_runtime_lookup():
    wrong = ScopeAnchors(scope_kind=ScopeKind.PLUGIN_INSTANCE, scope_id="other", tenant_id="tenant-1",
                         cabinet_id="cabinet-1", plugin_instance_id="other")
    evidence = Evidence(plugin_scope=ScopeResolutionResult(
        ScopeResolution.ACTIVE, active_entry(wrong), "wrong", CORRELATION))
    lifecycle, audit = Lifecycle(), Audit()
    result = await FailClosedResolvePluginInstance(lifecycle, audit, upstream=upstream(),
                                                   identity_evidence=evidence).resolve(
        context=context(), query=InstanceResolveQuery(identity(), 4, 7))
    assert result is None and lifecycle.calls == []
    assert audit.records[0].reason_code == "plugin_instance_registry_relation_unproven"
    assert evidence.definition_calls == 0


@pytest.mark.asyncio
async def test_writer_generation_cas_advances_exactly_one_and_denies_stale_worker():
    snap = RuntimeSnapshot(identity(), InstanceLifecycleState.PUBLISHED, RuntimeAvailability.ACTIVE, 4, 8)
    lifecycle = Lifecycle(WriterCasResult(True, snap, "ok"))
    result = await advance_writer_generation(lifecycle, identity=identity(), context=context(),
        expected_runtime_generation=4, expected_writer_generation=7, upstream=upstream(),
        correlation_id=CORRELATION, identity_evidence=Evidence())
    assert not result.changed and result.reason_code == "stale_worker_denied"
    assert lifecycle.calls[0][1]["next_writer_generation"] == 8
    assert lifecycle.calls[0][1]["expected_writer_generation"] == 7
    assert lifecycle.calls[0][1]["identity"] == identity()


@pytest.mark.asyncio
async def test_writer_cas_denies_before_storage_when_upstream_gate_incomplete():
    lifecycle = Lifecycle()
    result = await advance_writer_generation(lifecycle, identity=identity(), context=context(),
        expected_runtime_generation=4, expected_writer_generation=7,
        upstream=upstream(m04e_ready=False), correlation_id=CORRELATION)
    assert not result.changed and result.reason_code == "upstream_or_m05_gate_incomplete"
    assert lifecycle.calls == []


@pytest.mark.asyncio
async def test_runtime_generation_cas_advances_exactly_one_or_denies_wrong_instance():
    wrong = RuntimeSnapshot(identity(plugin_instance_id="other", scope_id="other"),
        InstanceLifecycleState.PUBLISHED, RuntimeAvailability.ACTIVE, 5, 7)
    lifecycle = Lifecycle(WriterCasResult(True, wrong, "ok"))
    result = await advance_runtime_generation(lifecycle, identity=identity(), context=context(),
        expected_runtime_generation=4, expected_writer_generation=7,
        upstream=upstream(), correlation_id=CORRELATION, identity_evidence=Evidence())
    assert not result.changed and result.reason_code == "stale_runtime_denied"
    assert lifecycle.calls[0][1]["next_runtime_generation"] == 5


@pytest.mark.asyncio
async def test_lifecycle_publish_requires_upstream_gate_and_cas_advances_writer():
    lifecycle = Lifecycle()
    identity_evidence = Evidence()
    blocked = await InstanceLifecycleService(lifecycle, upstream=None, correlation_id=CORRELATION,
                                             identity_evidence=identity_evidence).transition(
        identity=identity(), expected_lifecycle=InstanceLifecycleState.DRAFT,
        next_lifecycle=InstanceLifecycleState.PUBLISHED, context=context(),
        expected_runtime_generation=0, expected_writer_generation=0)
    assert not blocked.changed and blocked.reason_code == "upstream_or_m05_gate_incomplete"
    assert lifecycle.calls == []
    published = RuntimeSnapshot(identity(), InstanceLifecycleState.PUBLISHED, RuntimeAvailability.ACTIVE, 0, 1)
    lifecycle.result = WriterCasResult(True, published, "ok")
    result = await InstanceLifecycleService(lifecycle, upstream=upstream(), correlation_id=CORRELATION,
                                            identity_evidence=identity_evidence).transition(
        identity=identity(), expected_lifecycle=InstanceLifecycleState.DRAFT,
        next_lifecycle=InstanceLifecycleState.PUBLISHED, context=context(),
        expected_runtime_generation=0, expected_writer_generation=0)
    assert result.changed
    assert identity_evidence.scope_calls == identity_evidence.definition_calls == 1
    assert lifecycle.calls[-1][1]["next_writer_generation"] == 1
    assert lifecycle.calls[-1][1]["expected_lifecycle"] is InstanceLifecycleState.DRAFT


@pytest.mark.asyncio
async def test_lifecycle_publish_denies_bad_reciprocal_registry_relation_without_cas():
    wrong = ScopeAnchors(scope_kind=ScopeKind.PLUGIN_INSTANCE, scope_id="other", tenant_id="tenant-1",
                         cabinet_id="cabinet-1", plugin_instance_id="other")
    evidence = Evidence(plugin_scope=ScopeResolutionResult(
        ScopeResolution.ACTIVE, active_entry(wrong), "wrong", CORRELATION))
    lifecycle = Lifecycle()
    result = await InstanceLifecycleService(lifecycle, upstream=upstream(), correlation_id=CORRELATION,
                                            identity_evidence=evidence).transition(
        identity=identity(), expected_lifecycle=InstanceLifecycleState.DRAFT,
        next_lifecycle=InstanceLifecycleState.PUBLISHED, context=context(),
        expected_runtime_generation=0, expected_writer_generation=0)
    assert not result.changed
    assert result.reason_code == "plugin_instance_registry_relation_unproven"
    assert lifecycle.calls == [] and evidence.definition_calls == 0


@pytest.mark.asyncio
async def test_terminal_lifecycle_cannot_be_republished_or_resurrected():
    lifecycle = Lifecycle()
    result = await InstanceLifecycleService(lifecycle, upstream=upstream(), correlation_id=CORRELATION).transition(
        identity=identity(), expected_lifecycle=InstanceLifecycleState.REVOKED,
        next_lifecycle=InstanceLifecycleState.PUBLISHED, context=context(),
        expected_runtime_generation=4, expected_writer_generation=7)
    assert not result.changed and result.reason_code == "lifecycle_transition_not_allowed"
    assert lifecycle.calls == []


@pytest.mark.asyncio
async def test_import_requires_live_subject_and_session_evidence():
    class MissingSessionProof(Evidence):
        async def prove_subject_session(self, *, context, identity):
            return None

    missing_session_evidence = MissingSessionProof()
    audit = Audit()
    result = await InstanceImportService(
        Reader([candidate()]), Store(), missing_session_evidence, audit, upstream=upstream()
    ).run_page(checkpoint(), context=context())
    assert result.records[0].outcome is ImportOutcome.QUARANTINED
    assert result.records[0].reason_code == "subject_session_binding_unproven"
    assert result.records[0].binding_evidence is None
    assert missing_session_evidence.session_calls == 1
    assert audit.records[-1].reason_code == "subject_session_binding_unproven"


@pytest.mark.asyncio
async def test_import_requires_missing_subject_or_session_context_to_block_before_source_read():
    for invalid_context in (context(subject_id=None), context(session_id=None)):
        reader, store, audit = Reader([candidate()]), Store(), Audit()
        result = await InstanceImportService(
            reader, store, Evidence(), audit, upstream=upstream()
        ).run_page(checkpoint(), context=invalid_context)
        assert result.blocked_reason == "authenticated_runtime_context_missing"
        assert reader.calls == [] and store.commits == []
        assert audit.records[-1].outcome == "blocked"


@pytest.mark.asyncio
async def test_import_replay_of_quarantine_is_not_promoted_to_success():
    candidate_row = candidate()
    original = InstanceImportRecord(
        candidate_row.source_kind, candidate_row.source_id, candidate_row.schema_version,
        candidate_row.digest, checkpoint("src-1", 1), ImportOutcome.QUARANTINED,
        "subject_session_binding_unproven", CORRELATION,
        cursor_checkpoint=checkpoint("src-1", 1),
    )
    result = await InstanceImportService(
        Reader([candidate_row]), Store(prior=original), Evidence(), Audit(), upstream=upstream()
    ).run_page(checkpoint(), context=context())
    assert result.records[0].outcome is ImportOutcome.QUARANTINED
    assert result.records[0].binding_evidence is None
    assert result.records[0].reason_code == "subject_session_binding_unproven"


@pytest.mark.asyncio
async def test_import_replay_does_not_bypass_duplicate_instance_identity():
    first = imported_record("src-1")
    duplicate = candidate("src-2")
    store = Store(prior=first)
    result = await InstanceImportService(
        Reader([candidate("src-1"), duplicate]), store, Evidence(), Audit(), upstream=upstream()
    ).run_page(checkpoint(), context=context())
    assert [row.outcome for row in result.records] == [ImportOutcome.REPLAY, ImportOutcome.QUARANTINED]
    assert result.records[1].reason_code == "duplicate_plugin_instance_identity"


@pytest.mark.asyncio
async def test_import_quarantines_unsupported_source_lifecycle_without_throwing():
    unsupported = InstanceSourceRuntime("running", 4, 7)  # type: ignore[arg-type]
    result = await InstanceImportService(
        Reader([candidate(runtime=unsupported)]), Store(), Evidence(), Audit(), upstream=upstream()
    ).run_page(checkpoint(), context=context())
    assert result.records[0].outcome is ImportOutcome.QUARANTINED
    assert result.records[0].reason_code == "unsupported_lifecycle_state"
    assert result.records[0].binding_evidence is None


@pytest.mark.asyncio
async def test_import_duplicate_instance_id_in_same_page_is_quarantined():
    result = await InstanceImportService(
        Reader([candidate("src-1"), candidate("src-2")]), Store(), Evidence(), Audit(),
        upstream=upstream(),
    ).run_page(checkpoint(), context=context())
    assert [row.outcome for row in result.records] == [ImportOutcome.IMPORTED, ImportOutcome.QUARANTINED]
    assert result.records[1].reason_code == "duplicate_plugin_instance_identity"


@pytest.mark.asyncio
async def test_resolver_and_writer_cas_deny_stale_runtime_and_writer_epochs():
    audit = Audit()
    lifecycle = Lifecycle(WriterCasResult(True, RuntimeSnapshot(
        identity(), InstanceLifecycleState.PUBLISHED, RuntimeAvailability.ACTIVE, 3, 6), "ok"))
    resolved = await FailClosedResolvePluginInstance(
        lifecycle, audit, upstream=upstream(), identity_evidence=Evidence()
    ).resolve(context=context(), query=InstanceResolveQuery(identity(), 4, 7))
    assert resolved is None
    assert audit.records[-1].reason_code == "stale_runtime_generation"

    stale = await advance_writer_generation(
        lifecycle, identity=identity(), context=context(), expected_runtime_generation=3,
        expected_writer_generation=6, upstream=upstream(), correlation_id=CORRELATION,
        identity_evidence=Evidence(),
    )
    assert not stale.changed
    assert stale.reason_code == "stale_worker_denied"


@pytest.mark.asyncio
async def test_runtime_restart_increments_runtime_and_writer_generations_together():
    restarted = RuntimeSnapshot(identity(), InstanceLifecycleState.PUBLISHED,
                                RuntimeAvailability.ACTIVE, 5, 8)
    lifecycle = Lifecycle(WriterCasResult(True, restarted, "ok"))
    result = await advance_runtime_generation(
        lifecycle, identity=identity(), context=context(), expected_runtime_generation=4,
        expected_writer_generation=7, upstream=upstream(), correlation_id=CORRELATION,
        identity_evidence=Evidence(),
    )
    assert result.changed and result.snapshot == restarted
    call = lifecycle.calls[-1][1]
    assert call["expected_runtime_generation"] == 4
    assert call["expected_writer_generation"] == 7
    assert call["next_runtime_generation"] == 5
    assert call["next_writer_generation"] == 8


@pytest.mark.asyncio
async def test_runtime_restart_stale_cas_zero_rows_denies_worker():
    lifecycle = Lifecycle(WriterCasResult(False, None, "runtime_cas_conflict"))
    result = await advance_runtime_generation(
        lifecycle, identity=identity(), context=context(), expected_runtime_generation=4,
        expected_writer_generation=7, upstream=upstream(), correlation_id=CORRELATION,
        identity_evidence=Evidence(),
    )
    assert not result.changed and result.snapshot is None
    assert result.reason_code == "runtime_cas_conflict"


@pytest.mark.asyncio
async def test_generation_cas_denies_cross_tenant_instance_before_storage():
    lifecycle = Lifecycle()
    cross_tenant_identity = identity(
        tenant_id="tenant-other",
        installation_scope=ScopeAnchors(scope_kind=ScopeKind.CABINET, scope_id="cabinet-other",
                                        tenant_id="tenant-other", cabinet_id="cabinet-other"),
    )
    result = await advance_writer_generation(
        lifecycle, identity=cross_tenant_identity, context=context(),
        expected_runtime_generation=4, expected_writer_generation=7, upstream=upstream(),
        correlation_id=CORRELATION, identity_evidence=Evidence(),
    )
    assert not result.changed and result.reason_code == "authenticated_binding_mismatch"
    assert lifecycle.calls == []


@pytest.mark.asyncio
async def test_lifecycle_rejects_unsupported_state_before_cas():
    lifecycle = Lifecycle()
    result = await InstanceLifecycleService(
        lifecycle, upstream=upstream(), correlation_id=CORRELATION, identity_evidence=Evidence()
    ).transition(identity=identity(), expected_lifecycle="running",  # type: ignore[arg-type]
                 next_lifecycle=InstanceLifecycleState.PUBLISHED, context=context(),
                 expected_runtime_generation=4, expected_writer_generation=7)
    assert not result.changed and result.reason_code == "unsupported_lifecycle_state"
    assert lifecycle.calls == []


class ExactCandidateReader:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def read_exact(self, **query):
        self.calls.append(query)
        return self.row


class RetryStore:
    def __init__(self, original, *, attempts=()):
        self.original = original
        self.state = QuarantineRetryState(tuple(attempts))
        self.calls = []
        self.instance: InstanceImportRecord | None = None

    async def find_quarantine(self, **query):
        self.calls.append(("find_quarantine", query))
        return self.original

    async def load_retry_state(self, **query):
        self.calls.append(("load_retry_state", query))
        return self.state

    async def find_instance(self, **query):
        self.calls.append(("find_instance", query))
        return self.instance

    async def commit_retry_attempt(self, *, context, original, expected_attempt_count,
                                   attempt, audit_record):
        assert original is self.original
        assert expected_attempt_count == len(self.state.attempts)
        self.calls.append(("commit", attempt, audit_record))
        self.state = QuarantineRetryState((*self.state.attempts, attempt))
        return attempt


@pytest.mark.asyncio
async def test_quarantine_retry_retries_pinned_unchanged_source_and_preserves_original():
    original = InstanceImportRecord(
        "legacy-instance", "src-1", "schema-v1", candidate().digest, checkpoint("src-1", 1),
        ImportOutcome.QUARANTINED, "active_installation_scope_unproven", CORRELATION,
        cursor_checkpoint=checkpoint("src-1", 1),
    )
    exact = ExactCandidateReader(candidate())
    store = RetryStore(original)
    result = await InstanceQuarantineRetryService(
        exact, store, Evidence(), Audit(), upstream=upstream()
    ).retry(partition=PARTITION, source_snapshot_id=SNAPSHOT_ID, source_schema_version="schema-v1",
            source_snapshot_digest=D, source_kind="legacy-instance", source_id="src-1",
            schema_version="schema-v1", context=context())
    assert result.outcome is QuarantineRetryOutcome.RETRIED
    assert result.attempt is not None and result.attempt.outcome is ImportOutcome.IMPORTED
    assert result.attempt.attempt_number == 1
    assert result.attempt.binding_evidence is not None
    assert exact.calls[0]["source_snapshot_digest"] == D
    assert store.original is original and len(store.state.attempts) == 1
    audit = store.calls[-1][2]
    assert audit.event == "plugin_instance.quarantine_retry"
    assert not {"payload", "subject_id", "session_id", "tenant_id"} & set(audit.__dataclass_fields__)


@pytest.mark.asyncio
async def test_quarantine_retry_changed_digest_is_conflict_not_replacement():
    original = InstanceImportRecord(
        "legacy-instance", "src-1", "schema-v1", candidate().digest, checkpoint("src-1", 1),
        ImportOutcome.QUARANTINED, "missing_session", CORRELATION,
        cursor_checkpoint=checkpoint("src-1", 1),
    )
    changed = candidate(payload={"state": "changed"})
    store = RetryStore(original)
    result = await InstanceQuarantineRetryService(
        ExactCandidateReader(changed), store, Evidence(), Audit(), upstream=upstream()
    ).retry(partition=PARTITION, source_snapshot_id=SNAPSHOT_ID, source_schema_version="schema-v1",
            source_snapshot_digest=D, source_kind="legacy-instance", source_id="src-1",
            schema_version="schema-v1", context=context())
    assert result.outcome is QuarantineRetryOutcome.RETRIED
    assert result.attempt is not None and result.attempt.outcome is ImportOutcome.CONFLICT
    assert result.attempt.reason_code == "source_digest_changed"
    assert store.original is original


@pytest.mark.asyncio
async def test_quarantine_retry_requires_original_quarantine_and_exact_tenant():
    retryable = InstanceImportRecord(
        "legacy-instance", "src-1", "schema-v1", candidate().digest, checkpoint("src-1", 1),
        ImportOutcome.QUARANTINED, "missing_session", CORRELATION,
        cursor_checkpoint=checkpoint("src-1", 1),
    )
    store = RetryStore(retryable)
    service = InstanceQuarantineRetryService(ExactCandidateReader(candidate()), store, Evidence(), Audit(), upstream=upstream())
    cross_tenant = await service.retry(
        partition=("tenant-other", "definition-1"), source_snapshot_id=SNAPSHOT_ID,
        source_schema_version="schema-v1", source_snapshot_digest=D,
        source_kind="legacy-instance", source_id="src-1", schema_version="schema-v1",
        context=context(),
    )
    assert cross_tenant.blocked_reason == "trusted_tenant_partition_mismatch"
    assert store.calls == []

    store.original = replace(retryable, outcome=ImportOutcome.IMPORTED,
                             binding_evidence=imported_record().binding_evidence)
    not_quarantined = await service.retry(
        partition=PARTITION, source_snapshot_id=SNAPSHOT_ID, source_schema_version="schema-v1",
        source_snapshot_digest=D, source_kind="legacy-instance", source_id="src-1",
        schema_version="schema-v1", context=context(),
    )
    assert not_quarantined.blocked_reason == "quarantine_record_missing_or_not_retryable"


@pytest.mark.asyncio
async def test_quarantine_retry_conflict_on_duplicate_instance_does_not_bind():
    original = InstanceImportRecord(
        "legacy-instance", "src-2", "schema-v1", candidate("src-2").digest, checkpoint("src-2", 2),
        ImportOutcome.QUARANTINED, "duplicate_plugin_instance_identity", CORRELATION,
        cursor_checkpoint=checkpoint("src-2", 2),
    )
    store = RetryStore(original)
    store.instance = imported_record("src-1")
    result = await InstanceQuarantineRetryService(
        ExactCandidateReader(candidate("src-2")), store, Evidence(), Audit(), upstream=upstream()
    ).retry(partition=PARTITION, source_snapshot_id=SNAPSHOT_ID, source_schema_version="schema-v1",
            source_snapshot_digest=D, source_kind="legacy-instance", source_id="src-2",
            schema_version="schema-v1", context=context())
    assert result.attempt is not None and result.attempt.outcome is ImportOutcome.QUARANTINED
    assert result.attempt.reason_code == "duplicate_plugin_instance_identity"
    assert result.attempt.binding_evidence is None


@pytest.mark.asyncio
async def test_quarantine_retry_mismatched_subject_session_stays_quarantined():
    original = InstanceImportRecord(
        "legacy-instance", "src-1", "schema-v1", candidate().digest, checkpoint("src-1", 1),
        ImportOutcome.QUARANTINED, "missing_session", CORRELATION,
        cursor_checkpoint=checkpoint("src-1", 1),
    )
    class InactiveSessionEvidence(Evidence):
        async def prove_subject_session(self, *, context, identity):
            binding = await super().prove_subject_session(context=context, identity=identity)
            return replace(binding, session_active=False) if binding is not None else None
    store = RetryStore(original)
    result = await InstanceQuarantineRetryService(
        ExactCandidateReader(candidate()), store, InactiveSessionEvidence(), Audit(), upstream=upstream()
    ).retry(partition=PARTITION, source_snapshot_id=SNAPSHOT_ID, source_schema_version="schema-v1",
            source_snapshot_digest=D, source_kind="legacy-instance", source_id="src-1",
            schema_version="schema-v1", context=context())
    assert result.attempt is not None and result.attempt.outcome is ImportOutcome.QUARANTINED
    assert result.attempt.reason_code == "subject_session_binding_unproven"


@pytest.mark.asyncio
async def test_quarantine_retry_missing_session_and_subject_context_is_denied_and_audited():
    original = InstanceImportRecord(
        "legacy-instance", "src-1", "schema-v1", candidate().digest, checkpoint("src-1", 1),
        ImportOutcome.QUARANTINED, "missing_session", CORRELATION,
        cursor_checkpoint=checkpoint("src-1", 1),
    )
    audit = Audit()
    store = RetryStore(original)
    service = InstanceQuarantineRetryService(
        ExactCandidateReader(candidate()), store, Evidence(), audit, upstream=upstream()
    )
    for invalid_context in (context(subject_id=None), context(session_id=None)):
        result = await service.retry(
            partition=PARTITION, source_snapshot_id=SNAPSHOT_ID,
            source_schema_version="schema-v1", source_snapshot_digest=D,
            source_kind="legacy-instance", source_id="src-1", schema_version="schema-v1",
            context=invalid_context,
        )
        assert result.outcome is QuarantineRetryOutcome.BLOCKED
        assert result.blocked_reason == "authenticated_runtime_context_missing"
    assert store.calls == []
    assert len(audit.records) == 2
    assert all(record.outcome == "blocked" for record in audit.records)


@pytest.mark.asyncio
async def test_resolver_denies_missing_subject_session_proof_before_runtime_lookup():
    class MissingSessionEvidence(Evidence):
        async def prove_subject_session(self, *, context, identity):
            return None
    lifecycle, audit = Lifecycle(), Audit()
    result = await FailClosedResolvePluginInstance(
        lifecycle, audit, upstream=upstream(), identity_evidence=MissingSessionEvidence()
    ).resolve(context=context(), query=InstanceResolveQuery(identity(), 4, 7))
    assert result is None and lifecycle.calls == []
    assert audit.records[-1].reason_code == "subject_session_binding_unproven"


@pytest.mark.asyncio
async def test_resolver_denies_stale_writer_generation_from_exact_snapshot():
    snapshot = RuntimeSnapshot(identity(), InstanceLifecycleState.PUBLISHED,
                               RuntimeAvailability.ACTIVE, 4, 6)
    lifecycle, audit = Lifecycle(WriterCasResult(True, snapshot, "ok")), Audit()
    result = await FailClosedResolvePluginInstance(
        lifecycle, audit, upstream=upstream(), identity_evidence=Evidence()
    ).resolve(context=context(), query=InstanceResolveQuery(identity(), 4, 7))
    assert result is None and audit.records[-1].reason_code == "stale_writer_generation"


def test_m06_handoff_blocks_missing_upstream_final_delta_quarantine_and_conflict():
    record = imported_record()
    start = checkpoint("src-1", 1)
    delta, parity, fence = handoff_evidence(current_checkpoint=start)
    missing = build_m06_handoff_record(
        record_id="promotion-1", records=(record,), checkpoint=start, upstream=None,
        actor="migration-agent", recorded_at="fixed-time", decision_owner="coordinator",
        writer_generation=9, correlation_id=CORRELATION, final_delta=delta,
        parity=parity, legacy_fence=fence)
    wrong_delta = replace(delta, checkpoint=checkpoint("src-other", 1))
    incomplete = build_m06_handoff_record(
        record_id="promotion-1", records=(record,), checkpoint=start, upstream=upstream(),
        actor="migration-agent", recorded_at="fixed-time", decision_owner="coordinator",
        writer_generation=9, correlation_id=CORRELATION, final_delta=wrong_delta,
        parity=parity, legacy_fence=fence)
    unresolved_upstream = build_m06_handoff_record(
        record_id="promotion-1", records=(record,), checkpoint=start,
        upstream=upstream(m05_rows_ready=False), actor="migration-agent",
        recorded_at="fixed-time", decision_owner="coordinator", writer_generation=9,
        correlation_id=CORRELATION, final_delta=delta, parity=parity, legacy_fence=fence)
    assert missing.status is HandoffStatus.BLOCKED
    assert incomplete.status is HandoffStatus.BLOCKED
    assert unresolved_upstream.status is HandoffStatus.BLOCKED
    assert all(not row.canonical_positive_writes and row.m06_handoff is HandoffStatus.BLOCKED
               for row in (missing, incomplete, unresolved_upstream))


def test_m06_handoff_requires_zero_unresolved_and_exact_partition_completeness():
    base = imported_record()
    quarantined = InstanceImportRecord("legacy-instance", "src-2", "schema-v1", D,
        checkpoint("src-2", 2), ImportOutcome.QUARANTINED, "missing_binding", CORRELATION,
        cursor_checkpoint=checkpoint("src-2", 2))
    final_checkpoint = checkpoint("src-2", 2)
    delta, parity, fence = handoff_evidence(current_checkpoint=final_checkpoint, expected_count=2,
                                            imported_count=1, writer_generation=9)
    result = build_m06_handoff_record(
        record_id="promotion-2", records=(base, quarantined), checkpoint=final_checkpoint,
        upstream=upstream(), actor="agent", recorded_at="fixed-time", decision_owner="coordinator",
        writer_generation=9, correlation_id=CORRELATION, final_delta=delta,
        parity=parity, legacy_fence=fence)
    assert result.status is HandoffStatus.BLOCKED
    assert result.quarantined_count == 1 and result.canonical_positive_writes is False


def test_m06_handoff_retry_success_uses_append_only_attempt_and_counts_exact_row():
    original = InstanceImportRecord(
        "legacy-instance", "src-2", "schema-v1", candidate("src-2").digest, checkpoint("src-2", 2),
        ImportOutcome.QUARANTINED, "subject_session_binding_unproven", CORRELATION,
        cursor_checkpoint=checkpoint("src-2", 2),
    )
    binding = imported_record("src-2").binding_evidence
    assert binding is not None
    attempt = QuarantineRetryAttempt(
        "legacy-instance", "src-2", "schema-v1", original.source_digest, 1,
        ImportOutcome.IMPORTED, None, original.correlation_id, original.checkpoint, binding,
    )
    final_checkpoint = checkpoint("src-2", 2)
    delta, parity, fence = handoff_evidence(current_checkpoint=final_checkpoint, expected_count=2)
    result = build_m06_handoff_record(
        record_id="promotion-retry", records=(imported_record("src-1"), original),
        checkpoint=final_checkpoint, upstream=upstream(), actor="agent", recorded_at="fixed-time",
        decision_owner="coordinator", writer_generation=9, correlation_id=CORRELATION,
        final_delta=delta, parity=parity, legacy_fence=fence, retry_attempts=(attempt,),
    )
    assert result.status is HandoffStatus.READY
    assert result.imported_count == 2 and result.quarantined_count == 0
    assert result.retry_evidence_digest is not None
    assert result.canonical_positive_writes is False and result.legacy_read_write_owner == "legacy"


def test_m06_handoff_rejects_retry_digest_or_checkpoint_mismatch():
    original = InstanceImportRecord(
        "legacy-instance", "src-2", "schema-v1", candidate("src-2").digest, checkpoint("src-2", 2),
        ImportOutcome.QUARANTINED, "subject_session_binding_unproven", CORRELATION,
        cursor_checkpoint=checkpoint("src-2", 2),
    )
    binding = imported_record("src-2").binding_evidence
    assert binding is not None
    wrong_digest = QuarantineRetryAttempt(
        "legacy-instance", "src-2", "schema-v1", "sha256:" + "b" * 64, 1,
        ImportOutcome.IMPORTED, None, CORRELATION, original.checkpoint, binding,
    )
    with pytest.raises(ValueError, match="exact original quarantine"):
        build_m06_handoff_record(
            record_id="promotion-retry-conflict", records=(original,), checkpoint=checkpoint("src-2", 2),
            upstream=upstream(), actor="agent", recorded_at="fixed-time", decision_owner="coordinator",
            writer_generation=9, correlation_id=CORRELATION,
            final_delta=handoff_evidence(current_checkpoint=checkpoint("src-2", 2), expected_count=1)[0],
            parity=None, legacy_fence=None, retry_attempts=(wrong_digest,),
        )


def test_m06_handoff_records_ready_evidence_but_never_enables_positive_writes():
    row = imported_record()
    final_checkpoint = checkpoint("src-1", 1)
    delta, parity, fence = handoff_evidence(current_checkpoint=final_checkpoint, writer_generation=9)
    result = build_m06_handoff_record(
        record_id="promotion-ready-evidence-only", records=(row,), checkpoint=final_checkpoint,
        upstream=upstream(), actor="agent", recorded_at="fixed-time", decision_owner="coordinator",
        writer_generation=9, correlation_id=CORRELATION, final_delta=delta,
        parity=parity, legacy_fence=fence)
    assert result.status is HandoffStatus.READY and result.m06_handoff is HandoffStatus.READY
    assert result.legacy_read_write_owner == "legacy"
    assert result.canonical_positive_writes is False
    assert result.gate_evidence and all(gate.passed for gate in result.gate_evidence)


def test_m06_handoff_rejects_unfenced_or_mismatched_writer_generation():
    row = imported_record()
    final_checkpoint = checkpoint("src-1", 1)
    delta, parity, _ = handoff_evidence(current_checkpoint=final_checkpoint, writer_generation=9)
    live_workers = LegacyWriterFenceEvidence(PARTITION, 9, 1, D)
    wrong_writer = LegacyWriterFenceEvidence(PARTITION, 8, 0, D)
    for fence in (live_workers, wrong_writer):
        result = build_m06_handoff_record(
            record_id="promotion-fenced-check", records=(row,), checkpoint=final_checkpoint,
            upstream=upstream(), actor="agent", recorded_at="fixed-time", decision_owner="coordinator",
            writer_generation=9, correlation_id=CORRELATION, final_delta=delta,
            parity=parity, legacy_fence=fence)
        assert result.status is HandoffStatus.BLOCKED
        assert result.canonical_positive_writes is False


def test_audit_contract_has_no_raw_identity_payload_or_credentials():
    audit = RuntimeAuditRecord("plugin_instance.resolve", "denied", "stale_worker_denied", CORRELATION, 4, 7)
    assert set(audit.__dataclass_fields__) == {
        "event", "outcome", "reason_code", "correlation_id", "runtime_generation", "writer_generation"
    }
    assert not {"payload", "subject_id", "session_id", "tenant_id", "scope_id", "token"} & set(audit.__dataclass_fields__)


def test_scope_and_identity_shapes_never_allow_instance_scope_to_replace_installation_scope():
    with pytest.raises(ValueError):
        ScopeAnchors(scope_kind=ScopeKind.PROJECT, scope_id="project-1", tenant_id="tenant-1", project_id="project-1")
    with pytest.raises(ValueError):
        InstanceIdentity("instance-1", "definition-1", "version-1", "app-1", "1.0.0", "tenant-1",
                         ScopeKind.PLUGIN_INSTANCE, "another-instance", scope_anchors(),
                         identity().plugin_instance_scope, D, "subject-1", "session-1")
