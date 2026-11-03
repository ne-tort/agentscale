"""Behavioral M04E acceptance cases; authored only, not run during freeze."""

from __future__ import annotations

from dataclasses import replace

import pytest

import prodavan.application.migration.m04e_definition_version.backfill as backfill_module
import prodavan.application.migration.m04e_definition_version.repository as repository_module
from prodavan.application.dynamic_apps_definition import (
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillOutcome,
    DefinitionVersionEvidenceBackfill,
    EvidenceStatus,
    HandoffGate,
    HandoffStatus,
    export_m05_evidence,
    persist_m05_handoff,
)
from prodavan.application.migration.m04e_definition_version.repository import (
    ServerDefinitionVersionRepository,
)
from prodavan.domain.dynamic_apps import AppLifecycleStatus
from acceptance_fixtures import (
    DEFINITION,
    MANIFEST_VERSION,
    SNAPSHOT_DIGEST,
    TENANT,
    VERSION_ID,
    FixtureArtifactContentPort,
    FixtureCandidateProofPort,
    FixtureCandidateReader,
    FixtureDefinitionEvidencePort,
    FixtureDefinitionResolver,
    FixtureEvidenceStore,
    FixtureGatePort,
    FixtureM05HandoffPort,
    FixtureScopePort,
    backfill_context,
    checkpoint,
    definition_candidate,
    definition_version_proof,
    digest,
    gates_for,
    proof_for,
    resolved_pair,
    resolver_context,
    version_candidate,
)


def _backfill(rows, store, artifact=None, *, page_size=10):
    candidates = rows if isinstance(rows, tuple) else (rows,)
    return DefinitionVersionEvidenceBackfill(
        FixtureCandidateReader(candidates),
        store,
        gates=FixtureGatePort(),
        proofs=FixtureCandidateProofPort(),
        artifacts=artifact or FixtureArtifactContentPort(),
        page_size=page_size,
    )


def _resolver(*, result=None, artifact=None, proof=None, scope=None):
    resolved = result or resolved_pair()
    source = FixtureDefinitionResolver(resolved)
    evidence = FixtureDefinitionEvidencePort(proof or definition_version_proof())
    facade = ServerDefinitionVersionRepository(
        source,
        scopes=scope or FixtureScopePort(),
        evidence=evidence,
        artifacts=artifact or FixtureArtifactContentPort(),
    )
    return facade, source, evidence


@pytest.mark.asyncio
async def test_exact_replay_is_idempotent_and_changed_digest_conflicts_without_overwrite(monkeypatch):
    monkeypatch.setattr(backfill_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)
    store = FixtureEvidenceStore()
    original = version_candidate()
    first = await _backfill(original, store).run_page(checkpoint(), context=backfill_context())
    replay = await _backfill(original, store).run_page(checkpoint(), context=backfill_context())
    changed = version_candidate(payload_overrides={"description": "changed evidence"})
    conflict = await _backfill(changed, store).run_page(checkpoint(), context=backfill_context())
    conflict_replay = await _backfill(changed, store).run_page(checkpoint(), context=backfill_context())

    assert first.records[0].outcome is BackfillOutcome.ACCEPTED
    assert replay.records[0].outcome is BackfillOutcome.REPLAY
    assert replay.records[0].source_digest == original.source_digest
    assert replay.checkpoint == first.checkpoint
    assert conflict.records[0].outcome is BackfillOutcome.CONFLICT
    assert conflict.records[0].source_digest == original.source_digest
    assert conflict.records[0].observed_source_digest == changed.source_digest
    assert conflict_replay.records[0].outcome is BackfillOutcome.REPLAY
    assert conflict_replay.records[0].status is EvidenceStatus.CONFLICT
    assert conflict_replay.checkpoint == conflict.checkpoint
    unrelated = version_candidate(payload_overrides={"description": "another changed observation"})
    second_conflict = await _backfill(unrelated, store).run_page(
        checkpoint(), context=backfill_context(),
    )
    assert second_conflict.records[0].outcome is BackfillOutcome.CONFLICT
    assert second_conflict.records[0].observed_source_digest == unrelated.source_digest
    assert second_conflict.records[0].observed_source_digest != changed.source_digest
    assert store.first[(original.source_kind, original.source_id, original.schema_version)].source_digest == original.source_digest


@pytest.mark.asyncio
async def test_artifact_digest_mismatch_and_missing_artifact_are_quarantined(monkeypatch):
    monkeypatch.setattr(backfill_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)
    mismatch = await _backfill(
        version_candidate(), FixtureEvidenceStore(),
        FixtureArtifactContentPort(content=b"different artifact bytes"),
    ).run_page(checkpoint(), context=backfill_context())
    missing = await _backfill(
        version_candidate(), FixtureEvidenceStore(), FixtureArtifactContentPort(content=None),
    ).run_page(checkpoint(), context=backfill_context())

    assert mismatch.records[0].status is EvidenceStatus.QUARANTINED
    assert mismatch.records[0].reason_code == "artifact_content_digest_mismatch"
    assert mismatch.records[0].canonical_digest is None
    assert missing.records[0].status is EvidenceStatus.QUARANTINED
    assert missing.records[0].reason_code == "artifact_content_missing"
    assert missing.records[0].canonical_digest is None


@pytest.mark.asyncio
async def test_missing_artifact_quarantine_replay_preserves_operator_guidance_counts_and_cursor(monkeypatch):
    monkeypatch.setattr(backfill_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)
    store = FixtureEvidenceStore()
    candidate = version_candidate()
    start = checkpoint()
    missing_artifact = FixtureArtifactContentPort(content=None)
    first = await _backfill(candidate, store, missing_artifact).run_page(
        start, context=backfill_context(),
    )
    replay = await _backfill(candidate, store, missing_artifact).run_page(
        start, context=backfill_context(),
    )

    assert first.records[0].outcome is BackfillOutcome.QUARANTINED
    assert first.records[0].reason_code == "artifact_content_missing"
    assert first.records[0].blocker_evidence == (
        "Restore and review the proof-bound artifact content. No durable quarantine-retry operation is defined; preserve this evidence and request an approved retry path.",
    )
    assert replay.records[0].outcome is BackfillOutcome.REPLAY
    assert replay.records[0].status is EvidenceStatus.QUARANTINED
    assert replay.records[0].blocker_evidence == first.records[0].blocker_evidence
    assert replay.records[0].source_digest == candidate.source_digest
    assert replay.checkpoint == first.checkpoint

    first_export = export_m05_evidence(
        tenant_id=TENANT, app_definition_id=DEFINITION, records=first.records,
        source_snapshot_digest=SNAPSHOT_DIGEST, checkpoint=first.checkpoint, gates=(),
    )
    replay_export = export_m05_evidence(
        tenant_id=TENANT, app_definition_id=DEFINITION, records=replay.records,
        source_snapshot_digest=SNAPSHOT_DIGEST, checkpoint=replay.checkpoint, gates=(),
    )
    assert first_export.status is HandoffStatus.BLOCKED
    assert replay_export.status is HandoffStatus.BLOCKED
    assert len(first_export.accepted) == len(replay_export.accepted) == 0
    assert len(first_export.quarantined) == len(replay_export.quarantined) == 1
    assert first_export.canonical_digest == replay_export.canonical_digest
    assert first_export.canonical_positive_writes is replay_export.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_changed_artifact_bytes_conflict_then_replay_observation(monkeypatch):
    monkeypatch.setattr(backfill_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)
    store = FixtureEvidenceStore()
    candidate = version_candidate()
    first = await _backfill(candidate, store).run_page(checkpoint(), context=backfill_context())
    changed_bytes = FixtureArtifactContentPort(content=b"artifact drift under immutable URI")
    conflict = await _backfill(candidate, store, changed_bytes).run_page(
        checkpoint(), context=backfill_context(),
    )
    replay = await _backfill(candidate, store, changed_bytes).run_page(
        checkpoint(), context=backfill_context(),
    )

    assert first.records[0].outcome is BackfillOutcome.ACCEPTED
    assert conflict.records[0].outcome is BackfillOutcome.CONFLICT
    assert conflict.records[0].reason_code == "artifact_digest_changed"
    assert conflict.records[0].source_digest == candidate.source_digest
    assert conflict.records[0].observed_artifact_digest is not None
    assert replay.records[0].outcome is BackfillOutcome.REPLAY
    assert replay.records[0].status is EvidenceStatus.CONFLICT
    assert replay.checkpoint == conflict.checkpoint


@pytest.mark.asyncio
async def test_archived_version_and_bad_current_link_never_resolve(monkeypatch):
    monkeypatch.setattr(backfill_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)
    archived = await _backfill(
        version_candidate(source_state=backfill_module.SourceRowState.ARCHIVED),
        FixtureEvidenceStore(),
    ).run_page(checkpoint(), context=backfill_context())
    monkeypatch.setattr(repository_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)
    bad_link = resolved_pair(current_version_id="different-version")
    facade, source, evidence = _resolver(
        result=bad_link,
        proof=definition_version_proof(current_version_id="different-version"),
    )
    denied = await facade.resolve(
        authenticated_context=resolver_context(), asserted_tenant_id=TENANT,
        app_id="demo-app", manifest_version=MANIFEST_VERSION,
    )

    assert archived.records[0].status is EvidenceStatus.QUARANTINED
    assert archived.records[0].reason_code == "archived_source_row"
    assert denied.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert denied.reason is DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH
    assert denied.reference is None and evidence.proofs == 0 and source.calls


@pytest.mark.asyncio
async def test_missing_artifact_and_archived_lifecycle_fail_closed_at_exact_resolver(monkeypatch):
    monkeypatch.setattr(repository_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)
    missing, source, evidence = _resolver(artifact=FixtureArtifactContentPort(content=None))
    missing_result = await missing.resolve(
        authenticated_context=resolver_context(), asserted_tenant_id=TENANT,
        app_id="demo-app", manifest_version=MANIFEST_VERSION,
    )
    resolved = resolved_pair()
    revoked_version = replace(
        resolved.version, status=AppLifecycleStatus.REVOKED, published_at=None,
    )
    archived_pair = replace(resolved, version=revoked_version)
    archived, archived_source, archived_evidence = _resolver(result=archived_pair)
    archived_result = await archived.resolve(
        authenticated_context=resolver_context(), asserted_tenant_id=TENANT,
        app_id="demo-app", manifest_version=MANIFEST_VERSION,
    )

    assert missing_result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert missing_result.reference is None and evidence.proofs == 0 and source.calls
    assert archived_result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert archived_result.reason is DefinitionResolutionReason.DISALLOWED_VERSION_STATUS
    assert archived_result.reference is None and archived_evidence.proofs == 0
    assert archived_source.calls


@pytest.mark.asyncio
async def test_caller_tenant_mismatch_is_denied_before_resolution(monkeypatch):
    monkeypatch.setattr(repository_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)
    facade, source, evidence = _resolver()
    result = await facade.resolve(
        authenticated_context=resolver_context(), asserted_tenant_id="other-tenant",
        app_id="demo-app", manifest_version=MANIFEST_VERSION,
    )

    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason is DefinitionResolutionReason.CROSS_TENANT_DEFINITION
    assert result.reference is None and source.calls == [] and evidence.proofs == 0


@pytest.mark.asyncio
async def test_m05_handoff_marker_is_explicitly_blocked_and_write_disabled():
    progress = replace(checkpoint(sequence=3, after_source_id=VERSION_ID), phase_complete=True)
    export = export_m05_evidence(
        tenant_id=TENANT, app_definition_id=DEFINITION, records=(),
        source_snapshot_digest=SNAPSHOT_DIGEST, checkpoint=progress,
        gates=(HandoffGate("MIG-M04-01", False, reason="blocked upstream"),),
    )
    port = FixtureM05HandoffPort()
    result = await persist_m05_handoff(
        export, store=port, actor="migration-fixture", recorded_at="2026-11-03T00:00:00Z",
        decision_owner="coordinator-fixture", correlation_id="corr-m04e-1", writer_generation=1,
    )

    assert export.status is HandoffStatus.BLOCKED
    assert result.status is HandoffStatus.BLOCKED
    assert result.handoff_marker is not None
    assert result.handoff_marker.task_id == "MIG-M04E-02"
    assert result.handoff_marker.canonical_positive_writes is False
    assert result.handoff_marker.checkpoint == progress
    assert len(port.markers) == 1


@pytest.mark.asyncio
async def test_backfill_fails_closed_without_artifact_port_and_reports_upstream_gate(monkeypatch):
    context = backfill_context()
    store = FixtureEvidenceStore()
    missing_artifact_port = DefinitionVersionEvidenceBackfill(
        FixtureCandidateReader((version_candidate(),)), store,
        gates=FixtureGatePort(),
        proofs=FixtureCandidateProofPort(), artifacts=None,
    )
    blocked = await missing_artifact_port.run_page(checkpoint(), context=context)
    assert blocked.records[0].status is EvidenceStatus.BLOCKED
    assert blocked.records[0].reason_code == "artifact_content_port_missing"
    assert blocked.checkpoint == checkpoint()

    monkeypatch.setattr(backfill_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", False)
    denied = await _backfill(version_candidate(), store).run_page(checkpoint(), context=context)
    assert denied.records[0].status is EvidenceStatus.BLOCKED
    assert denied.records[0].reason_code == "upstream_owner_scope_gate_unproven"
    assert denied.checkpoint == checkpoint()
    assert store.blockers == ["artifact_content_port_missing", "upstream_owner_scope_gate_unproven"]


@pytest.mark.asyncio
async def test_bounded_pages_resume_and_drain_definition_before_version(monkeypatch):
    monkeypatch.setattr(backfill_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True)
    context = backfill_context()
    store = FixtureEvidenceStore()
    service = _backfill(
        (definition_candidate(), version_candidate()), store, page_size=1,
    )
    start = replace(checkpoint(), phase="definition")

    definition_page = await service.run_page(start, context=context)
    transition = await service.run_page(definition_page.checkpoint, context=context)
    version_page = await service.run_page(transition.checkpoint, context=context)
    drained = await service.run_page(version_page.checkpoint, context=context)

    assert definition_page.records[0].source_id == DEFINITION
    assert definition_page.checkpoint.phase == "definition"
    assert transition.checkpoint.phase == "version"
    assert transition.checkpoint.after_source_id is None
    assert version_page.records[0].source_id == VERSION_ID
    assert not version_page.complete
    assert drained.complete and drained.checkpoint.phase_complete
    assert drained.checkpoint.phase == "version"


def test_version_evidence_has_no_version_id_only_query_or_positive_writer():
    assert "version_id" not in ServerDefinitionVersionRepository.resolve.__annotations__
    assert not any(name.startswith(("save", "create", "update", "delete", "publish"))
                   for name in dir(ServerDefinitionVersionRepository))