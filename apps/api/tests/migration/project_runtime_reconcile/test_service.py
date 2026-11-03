"""MIG-PROJECT-RUNTIME-RECONCILE-01 acceptance tests; authored, not executed."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from typing import Callable

import pytest

from prodavan.application.migration.project_runtime_reconcile.service import (
    ClaimResult,
    ClaimStatus,
    ConfigProvenance,
    DesiredRuntimeSnapshot,
    FencedClaim,
    MaterializationReadSnapshot,
    ModuleBindingEvidence,
    ObservedRuntime,
    PodPresence,
    ProjectRuntimeReconcileService,
    ProviderBindingEvidence,
    ReconcileAction,
    ReconcileOutcome,
    ReconcileRecord,
    ReconcileReplay,
    ReconcilePlan,
    RuntimeBindingsReadSnapshot,
    RuntimeExecutionReceipt,
    SourceVersionEvidence,
    _make_plan,
    compare_runtime,
    observed_runtime_digest,
)
from prodavan.application.migration.runtime_api.contracts import RuntimeApiProjectScope
from prodavan.application.runtime_revision.interfaces import (
    CapabilityBundle,
    MaterializationEntry,
    MaterializationStatus,
    ProjectRuntimeRevision,
    RevisionState,
)

IMAGE = "sha256:" + "a" * 64
CONTENT = "sha256:" + "b" * 64
CONFIG = "sha256:" + "c" * 64
MANIFEST = {"capabilities": ["agent"]}
BUNDLE_DIGEST = "sha256:" + hashlib.sha256(
    json.dumps(MANIFEST, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
).hexdigest()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def scope():
    return RuntimeApiProjectScope("project-1", "company-1", "cabinet-1", "pod-logical-1", "project-1/runtime")


def version(source_id="source-1", revision="v7", digest=CONTENT):
    return SourceVersionEvidence("migration_test", source_id, revision, digest)


def inputs(scope, *, image: str | None = IMAGE, revision_id="revision-7", pod_uid="uid-7", entries=None):
    revision = ProjectRuntimeRevision(
        revision_id=revision_id,
        project_id=scope.project_id,
        revision=7,
        bundle=CapabilityBundle("bundle-7", scope.project_id, 7, MANIFEST, BUNDLE_DIGEST),
        state=RevisionState.READY,
        pod_id=scope.pod_id,
        pod_uid=pod_uid,
        hydrate_generation=4,
        bridge_generation=2,
        runtime_image_digest=image,
    )
    if entries is None:
        entries = (MaterializationEntry(
            entry_id="entry-1", revision_id=revision_id, workspace_path=".prodavan/config.yaml",
            kind="runtime_config", generation=4, status=MaterializationStatus.APPLIED,
            content_hash=CONFIG, provenance={"source": "project_runtime_revision"},
        ),)
    materialization = MaterializationReadSnapshot(scope, revision_id, 4, tuple(entries), True)
    bindings = RuntimeBindingsReadSnapshot(
        scope,
        revision_id,
        (ModuleBindingEvidence("mod-agent", "mp-1", "global", "instance-1", version("module-1")),),
        (ProviderBindingEvidence("provider-1", "key-binding-1", "model-1", version("provider-1")),),
        (ConfigProvenance("agent.runtime", version("config-source-1"), {"mode": "agent", "secret_ref": "vault://runtime/key"}),),
    )
    return revision, materialization, bindings


def desired(scope, **kwargs):
    revision, materialization, bindings = inputs(scope, **kwargs)
    return DesiredRuntimeSnapshot.build(scope=scope, revision=revision, materialization=materialization, bindings=bindings)


def observed(scope, target, **overrides):
    values = dict(
        scope=scope,
        presence=PodPresence.PRESENT,
        project_id=scope.project_id,
        pod_id=scope.pod_id,
        pod_uid="uid-7",
        revision_id=target.revision_id,
        config_digest=target.config_digest,
        workspace_digest=target.workspace_digest,
        workspace_generation=target.hydrate_generation,
        runtime_image_digest=target.runtime_image_digest,
        healthy=True,
    )
    values.update(overrides)
    return ObservedRuntime(**values)  # type: ignore[arg-type]


class FakeSource:
    def __init__(self, scope, *, altered=None):
        self.scope = scope
        self.revision, self.materialization, self.bindings = inputs(scope)
        self.altered = altered or {}
        self.reads = []

    async def get_latest(self, *, project_id):
        self.reads.append(("revision", project_id))
        return self.altered.get("revision", self.revision)

    async def read_materialization(self, *, scope, revision_id):
        self.reads.append(("materialization", scope, revision_id))
        return self.altered.get("materialization", self.materialization)

    async def read_bindings(self, *, scope, revision_id):
        self.reads.append(("bindings", scope, revision_id))
        return self.altered.get("bindings", self.bindings)


class FakeObserved:
    def __init__(self, state):
        self.state = state
        self.calls = []

    async def observe(self, *, scope):
        self.calls.append(scope)
        return self.state


class FakeStore:
    def __init__(self, scope, *, generation=0, lkg=None, claim_status=ClaimStatus.CLAIMED):
        self.record = ReconcileRecord(
            scope, generation, lkg, lkg.digest if lkg else None,
            observed(scope, lkg) if lkg is not None else None,
        )
        self.claim_status = claim_status
        self.claim_calls = []
        self.finish_calls = []
        self.claim_override = None
        self.replays = {}

    async def load(self, *, scope):
        return self.record

    async def find_replay(self, *, scope, idempotency_key):
        return self.replays.get((scope, idempotency_key))

    async def claim(
        self, *, scope, expected_generation, desired_digest, plan_digest, idempotency_key,
        recovery_digest, observed_digest,
    ):
        self.claim_calls.append((scope, expected_generation, desired_digest, plan_digest,
                                 idempotency_key, recovery_digest, observed_digest))
        if self.claim_override is not None:
            return self.claim_override
        if self.claim_status is not ClaimStatus.CLAIMED:
            return ClaimResult(self.claim_status, reason_code="test_conflict")
        claim = FencedClaim(scope, expected_generation, expected_generation + 1, "fence-1",
                           desired_digest, plan_digest, idempotency_key, observed_digest, recovery_digest)
        return ClaimResult(ClaimStatus.CLAIMED, claim)

    async def finish(self, *, claim, outcome, applied_digest, last_known_good, applied_observed):
        self.finish_calls.append((claim, outcome, applied_digest, last_known_good, applied_observed))
        self.record = ReconcileRecord(
            claim.scope, claim.generation, last_known_good, applied_digest, applied_observed, outcome
        )
        return True


class FakeRuntime:
    def __init__(self, scope, *, fail_actions=(), mutate_receipt=None):
        self.scope = scope
        self.fail_actions = set(fail_actions)
        self.mutate_receipt = mutate_receipt
        self.calls = []
        self.observed_factory: Callable[[DesiredRuntimeSnapshot], ObservedRuntime] | None = None

    async def execute(self, *, action, scope, snapshot, observed, claim):
        self.calls.append((action, scope, snapshot, observed, claim))
        target = self.observed_factory(snapshot) if self.observed_factory else None
        receipt = RuntimeExecutionReceipt(
            scope=scope, action=action, fence_token=claim.fence_token, generation=claim.generation,
            snapshot_digest=snapshot.digest, accepted=action not in self.fail_actions,
            healthy=action not in self.fail_actions and action is not ReconcileAction.QUARANTINE,
            observed=target, reason_code="test_result",
            compared_observation_digest=observed_runtime_digest(observed) if observed is not None
            else claim.observed_digest,
        )
        return self.mutate_receipt(receipt) if self.mutate_receipt else receipt


def service(scope, state, *, generation=0, lkg=None, fail_actions=(), claim_status=ClaimStatus.CLAIMED,
            mutate_receipt=None):
    source = FakeSource(scope)
    observed_port = FakeObserved(state)
    store = FakeStore(scope, generation=generation, lkg=lkg, claim_status=claim_status)
    runtime = FakeRuntime(scope, fail_actions=fail_actions, mutate_receipt=mutate_receipt)
    runtime.observed_factory = lambda snapshot: observed(scope, snapshot, pod_uid=snapshot.expected_pod_uid or "uid-new")
    return ProjectRuntimeReconcileService(source=source, observed=observed_port, store=store, runtime=runtime), source, observed_port, store, runtime


def test_snapshot_digest_is_deterministic_across_binding_and_materialization_order(scope):
    revision, materialization, binding = inputs(scope)
    second_module = ModuleBindingEvidence("mod-b", "mp-b", "local", "instance-b", version("module-b"))
    second_entry = MaterializationEntry(
        entry_id="entry-2", revision_id=revision.revision_id, workspace_path="workspace/README.md",
        kind="file", generation=4, status=MaterializationStatus.APPLIED, content_hash=CONTENT,
    )
    left = DesiredRuntimeSnapshot.build(
        scope=scope, revision=revision,
        materialization=replace(materialization, entries=materialization.entries + (second_entry,)),
        bindings=replace(binding, module_bindings=binding.module_bindings + (second_module,)),
    )
    right = DesiredRuntimeSnapshot.build(
        scope=scope, revision=revision,
        materialization=replace(materialization, entries=(second_entry,) + materialization.entries),
        bindings=replace(binding, module_bindings=(second_module,) + binding.module_bindings),
    )
    assert left.digest == right.digest
    assert left.workspace_digest == right.workspace_digest


def test_snapshot_binds_project_revision_modules_provider_config_workspace_and_image(scope):
    target = desired(scope)
    assert target.scope == scope
    assert target.revision_id == "revision-7"
    assert target.module_bindings[0].version.source_version == "v7"
    assert target.provider_bindings[0].model_id == "model-1"
    assert target.config_provenance[0].source.content_digest == CONTENT
    assert target.config_digest.startswith("sha256:")
    assert target.workspace_digest.startswith("sha256:")
    assert target.materialization_generation == 4
    assert target.hydrate_generation == 4
    assert target.runtime_image_digest == IMAGE


def test_latest_and_missing_exact_project_scope_are_rejected(scope):
    revision, materialization, bindings = inputs(scope, image="runtime:latest")
    with pytest.raises(ValueError, match="runtime_image_digest_invalid"):
        DesiredRuntimeSnapshot.build(scope=scope, revision=revision, materialization=materialization, bindings=bindings)
    with pytest.raises(ValueError, match="project_runtime_revision_scope_mismatch"):
        DesiredRuntimeSnapshot.build(
            scope=replace(scope, project_id="other-project"), revision=revision,
            materialization=materialization, bindings=bindings,
        )


def test_binding_and_materialization_scope_must_match_all_project_scope_parts(scope):
    revision, materialization, bindings = inputs(scope)
    other_scope = replace(scope, cabinet_id="another-cabinet")
    with pytest.raises(ValueError, match="materialization_scope_or_revision_mismatch"):
        DesiredRuntimeSnapshot.build(
            scope=scope, revision=revision,
            materialization=replace(materialization, scope=other_scope), bindings=bindings,
        )
    with pytest.raises(ValueError, match="runtime_bindings_scope_or_revision_mismatch"):
        DesiredRuntimeSnapshot.build(
            scope=scope, revision=revision, materialization=materialization,
            bindings=replace(bindings, scope=other_scope),
        )


def test_manifest_and_desired_digest_tampering_are_rejected(scope):
    revision, materialization, bindings = inputs(scope)
    with pytest.raises(ValueError, match="capability_bundle_manifest_digest_mismatch"):
        DesiredRuntimeSnapshot.build(
            scope=scope, revision=replace(revision, bundle=replace(revision.bundle, digest=CONTENT)),
            materialization=materialization, bindings=bindings,
        )
    target = desired(scope)
    with pytest.raises(ValueError, match="desired_runtime_snapshot_digest_mismatch"):
        replace(target, digest=CONTENT)


def test_incomplete_or_cross_revision_materialization_fails_closed(scope):
    revision, materialization, bindings = inputs(scope)
    with pytest.raises(ValueError, match="materialization_snapshot_incomplete"):
        DesiredRuntimeSnapshot.build(scope=scope, revision=revision,
                                     materialization=replace(materialization, complete=False), bindings=bindings)
    with pytest.raises(ValueError, match="materialization_entry_version_mismatch"):
        bad_entry = replace(materialization.entries[0], revision_id="stale-revision")
        DesiredRuntimeSnapshot.build(scope=scope, revision=revision,
                                     materialization=replace(materialization, entries=(bad_entry,)), bindings=bindings)


def test_materialization_and_hydrate_generations_are_distinct_evidence(scope):
    revision, materialization, bindings = inputs(scope)
    target = DesiredRuntimeSnapshot.build(
        scope=scope, revision=replace(revision, hydrate_generation=9),
        materialization=replace(materialization, generation=4), bindings=bindings,
    )
    assert target.materialization_generation == 4
    assert target.hydrate_generation == 9
    assert compare_runtime(target, observed(scope, target, workspace_generation=4))[0] is ReconcileAction.REPLACE


def test_missing_provider_module_or_config_version_evidence_is_rejected(scope):
    revision, materialization, binding = inputs(scope)
    with pytest.raises(ValueError, match="runtime_binding_or_config_version_evidence_missing"):
        DesiredRuntimeSnapshot.build(scope=scope, revision=revision, materialization=materialization,
                                     bindings=replace(binding, provider_bindings=()))
    with pytest.raises(ValueError, match="source_version_missing_or_unversioned"):
        SourceVersionEvidence("provider", "provider-1", "latest", CONTENT)
    with pytest.raises(ValueError, match="runtime_bindings_snapshot_incomplete"):
        replace(binding, complete=False)


def test_inline_secret_config_is_rejected_but_external_ref_is_bound(scope):
    with pytest.raises(ValueError, match="inline_secret_config_forbidden"):
        ConfigProvenance("runtime", version(), {"api_token": "do-not-store"})
    config = ConfigProvenance("runtime", version(), {"api_token_ref": "vault://project/runtime"})
    assert config.values["api_token_ref"] == "vault://project/runtime"
    with pytest.raises(ValueError, match="unversioned_config_value_forbidden"):
        ConfigProvenance("runtime", version(), {"image": "agent-runtime:latest"})


def test_compare_healthy_observed_identity_returns_noop(scope):
    target = desired(scope)
    action, mismatches = compare_runtime(target, observed(scope, target))
    assert action is ReconcileAction.NOOP
    assert mismatches == ()


def test_compare_absent_pod_applies_and_owned_config_workspace_image_or_uid_mismatch_replaces(scope):
    target = desired(scope)
    assert compare_runtime(target, ObservedRuntime(scope, PodPresence.ABSENT))[0] is ReconcileAction.APPLY
    for changed in (
        {"config_digest": "sha256:" + "d" * 64},
        {"workspace_digest": "sha256:" + "d" * 64},
        {"workspace_generation": target.hydrate_generation + 1},
        {"runtime_image_digest": "sha256:" + "d" * 64},
    ):
        action, mismatches = compare_runtime(target, observed(scope, target, **changed))
        assert action is ReconcileAction.REPLACE
        assert mismatches
    assert compare_runtime(target, observed(scope, target, pod_uid="stale-uid"),
                           expected_pod_uid="uid-7")[0] is ReconcileAction.REPLACE


def test_foreign_or_ambiguous_observed_identity_is_quarantined(scope):
    target = desired(scope)
    foreign = observed(scope, target, project_id="foreign-project")
    action, mismatches = compare_runtime(target, foreign)
    assert action is ReconcileAction.QUARANTINE
    assert "observed_pod_owner_mismatch" in mismatches
    assert compare_runtime(target, ObservedRuntime(scope, PodPresence.UNKNOWN))[0] is ReconcileAction.QUARANTINE
    assert compare_runtime(target, observed(scope, target, pod_uid=None))[0] is ReconcileAction.QUARANTINE


def test_reconcile_noop_does_not_claim_or_execute_when_generation_records_same_snapshot(scope):
    target = desired(scope)
    app, _, observed_port, store, runtime = service(scope, observed(scope, target))
    store.record = ReconcileRecord(scope, 0, target, target.digest, observed(scope, target))
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="noop-1"))
    assert result.outcome is ReconcileOutcome.NOOP
    assert result.plan is not None and result.plan.action is ReconcileAction.NOOP
    assert store.claim_calls == [] and runtime.calls == []
    assert observed_port.calls == [scope]


def test_reconcile_apply_is_fenced_and_commits_last_known_good_after_health(scope):
    app, _, _, store, runtime = service(scope, ObservedRuntime(scope, PodPresence.ABSENT))
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="apply-1"))
    assert result.outcome is ReconcileOutcome.APPLIED
    assert result.plan is not None and result.plan.action is ReconcileAction.APPLY
    assert len(store.claim_calls) == 1 and len(runtime.calls) == 1
    assert result.desired is not None and result.observed is not None
    assert store.finish_calls[0][3].digest == result.desired.digest
    assert store.finish_calls[0][4].pod_uid == result.observed.pod_uid
    assert runtime.calls[0][4].fence_token == "fence-1"


def test_reconcile_replaces_config_mismatch_and_surfaces_health_mismatch(scope):
    target = desired(scope)
    current = observed(scope, target, config_digest="sha256:" + "d" * 64, healthy=False)
    app, _, _, _, runtime = service(scope, current)
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="replace-1"))
    assert result.outcome is ReconcileOutcome.REPLACED
    assert result.plan is not None and result.plan.action is ReconcileAction.REPLACE
    assert "runtime_config_mismatch" in result.mismatch_codes
    assert "runtime_health_mismatch" in result.mismatch_codes
    assert runtime.calls[0][0] is ReconcileAction.REPLACE


def test_foreign_observation_quarantines_without_inferring_scope(scope):
    target = desired(scope, pod_uid=None)
    app, _, _, store, runtime = service(scope, observed(scope, target, project_id="other"))
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="quarantine-foreign"))
    assert result.outcome is ReconcileOutcome.QUARANTINED
    assert result.plan is not None and result.plan.action is ReconcileAction.QUARANTINE
    assert len(store.claim_calls) == 1 and [call[0] for call in runtime.calls] == [ReconcileAction.QUARANTINE]
    assert store.finish_calls[0][1] is ReconcileOutcome.QUARANTINED
    assert store.record.last_outcome is ReconcileOutcome.QUARANTINED


def test_ambiguous_observation_is_quarantined_through_fenced_port(scope):
    app, _, _, store, runtime = service(scope, ObservedRuntime(scope, PodPresence.UNKNOWN))
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="quarantine-unknown"))
    assert result.outcome is ReconcileOutcome.QUARANTINED
    assert result.plan is not None and result.plan.action is ReconcileAction.QUARANTINE
    assert store.claim_calls[0][0] == scope
    assert runtime.calls[0][1] == scope


def test_store_generation_conflict_prevents_runtime_side_effect(scope):
    target = desired(scope)
    app, _, _, store, runtime = service(scope, ObservedRuntime(scope, PodPresence.ABSENT), generation=3)
    result = run(app.reconcile(scope=scope, expected_generation=2, idempotency_key="stale-cas"))
    assert result.outcome is ReconcileOutcome.CONFLICT
    assert result.reason_code == "generation_conflict"
    assert store.claim_calls == [] and runtime.calls == []


def test_stale_or_malformed_fencing_claim_is_rejected(scope):
    app, _, _, store, runtime = service(scope, ObservedRuntime(scope, PodPresence.ABSENT))
    store.claim_override = ClaimResult(
        ClaimStatus.CLAIMED,
        FencedClaim(scope, 0, 1, "old-fence", CONTENT, CONTENT, "apply-1", CONTENT),
    )
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="apply-1"))
    assert result.outcome is ReconcileOutcome.CONFLICT
    assert runtime.calls == [] and store.finish_calls == []


def test_claim_recovery_digest_is_bound_to_lkg_snapshot(scope):
    lkg = desired(scope, revision_id="revision-6", pod_uid=None)
    app, _, _, store, runtime = service(
        scope, ObservedRuntime(scope, PodPresence.ABSENT), generation=5, lkg=lkg,
    )
    result = run(app.reconcile(scope=scope, expected_generation=5, idempotency_key="recovery-fence"))
    assert result.outcome is ReconcileOutcome.APPLIED
    assert store.claim_calls[0][5] == lkg.digest
    assert runtime.calls[0][4].recovery_digest == lkg.digest


def test_failed_health_restores_last_known_good_under_same_fence(scope):
    lkg = desired(scope, revision_id="revision-6", pod_uid=None)
    app, _, _, store, runtime = service(
        scope, ObservedRuntime(scope, PodPresence.ABSENT), generation=5, lkg=lkg,
        fail_actions=(ReconcileAction.APPLY,),
    )
    result = run(app.reconcile(scope=scope, expected_generation=5, idempotency_key="rollback-1"))
    assert result.outcome is ReconcileOutcome.ROLLED_BACK
    assert [call[0] for call in runtime.calls] == [ReconcileAction.APPLY, ReconcileAction.ROLLBACK]
    assert runtime.calls[0][4] == runtime.calls[1][4]
    assert store.record.last_known_good is not None and store.record.last_known_good.digest == lkg.digest


def test_failed_health_without_valid_last_known_good_quarantines(scope):
    app, _, _, store, runtime = service(
        scope, ObservedRuntime(scope, PodPresence.ABSENT), fail_actions=(ReconcileAction.APPLY,),
    )
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="quarantine-failed-apply"))
    assert result.outcome is ReconcileOutcome.QUARANTINED
    assert [call[0] for call in runtime.calls] == [ReconcileAction.APPLY, ReconcileAction.QUARANTINE]
    assert store.record.last_known_good is None
    assert store.record.last_outcome is ReconcileOutcome.QUARANTINED


def test_execution_receipt_with_wrong_scope_or_fence_cannot_commit(scope):
    def corrupt(receipt):
        return replace(receipt, fence_token="stale-fence")

    app, _, _, store, runtime = service(scope, ObservedRuntime(scope, PodPresence.ABSENT), mutate_receipt=corrupt)
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="bad-receipt"))
    assert result.outcome is ReconcileOutcome.QUARANTINED
    assert result.reason_code == "runtime_unhealthy_quarantined"
    assert store.record.applied_digest is None
    assert [call[0] for call in runtime.calls] == [ReconcileAction.APPLY, ReconcileAction.QUARANTINE]


def test_changed_pod_uid_after_apply_is_replaced_not_quietly_accepted(scope):
    target = desired(scope)
    app, _, _, store, _ = service(scope, observed(scope, target, pod_uid="uid-new"))
    store.record = ReconcileRecord(scope, 3, target, target.digest, observed(scope, target, pod_uid="uid-old"))
    result = run(app.reconcile(scope=scope, expected_generation=3, idempotency_key="pod-uid-replacement"))
    assert result.plan is not None
    assert result.plan.action is ReconcileAction.REPLACE
    assert "pod_uid_mismatch" in result.mismatch_codes


def test_revision_and_binding_source_reads_are_project_scoped_and_read_only(scope):
    app, source, _, _, _ = service(scope, ObservedRuntime(scope, PodPresence.ABSENT))
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="scope-read"))
    assert result.desired is not None and result.desired.scope == scope
    assert source.reads[0] == ("revision", scope.project_id)
    assert source.reads[1][1:] == (scope, "revision-7")
    assert source.reads[2][1:] == (scope, "revision-7")


def test_idempotent_replay_does_not_execute_again(scope):
    target = desired(scope)
    app, _, observed_port, store, runtime = service(scope, ObservedRuntime(scope, PodPresence.ABSENT))
    observed_state = observed_port.state
    observed_digest = observed_runtime_digest(observed_state)
    plan = _make_plan(scope, ReconcileAction.APPLY, 0, target.digest,
                      ("pod_absent",), "replay-1", observed_digest=observed_digest)
    claim = FencedClaim(scope, 0, 1, "fence-replay", target.digest, plan.plan_digest,
                        "replay-1", observed_digest, None)
    store.replays[(scope, "replay-1")] = ReconcileReplay(
        scope, plan, claim, ReconcileOutcome.APPLIED, None, target.digest,
    )
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="replay-1"))
    assert result.reason_code == "idempotent_replay"
    assert result.outcome is ReconcileOutcome.APPLIED
    assert observed_port.calls == []
    assert runtime.calls == []


def test_replay_with_wrong_scope_or_claim_is_denied(scope):
    target = desired(scope)
    app, _, observed_port, store, runtime = service(scope, ObservedRuntime(scope, PodPresence.ABSENT))
    obs_digest = observed_runtime_digest(observed_port.state)
    plan = _make_plan(scope, ReconcileAction.APPLY, 0, target.digest,
                      ("pod_absent",), "replay-bad", observed_digest=obs_digest)
    bad_claim = FencedClaim(scope, 0, 1, "fence-replay", CONTENT, plan.plan_digest,
                            "replay-bad", obs_digest)
    store.replays[(scope, "replay-bad")] = ReconcileReplay(
        scope, plan, bad_claim, ReconcileOutcome.APPLIED, None, target.digest,
    )
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="replay-bad"))
    assert result.outcome is ReconcileOutcome.BLOCKED
    assert result.reason_code == "idempotency_replay_binding_mismatch"
    assert runtime.calls == []


def test_missing_revision_or_image_evidence_blocks_before_observed_runtime_read(scope):
    source = FakeSource(scope, altered={"revision": None})
    observed_port = FakeObserved(ObservedRuntime(scope, PodPresence.ABSENT))
    store = FakeStore(scope)
    runtime = FakeRuntime(scope)
    app = ProjectRuntimeReconcileService(source=source, observed=observed_port, store=store, runtime=runtime)
    result = run(app.reconcile(scope=scope, expected_generation=0, idempotency_key="missing-revision"))
    assert result.outcome is ReconcileOutcome.BLOCKED
    assert observed_port.calls == [] and store.claim_calls == [] and runtime.calls == []

    revision, materialization, bindings = inputs(scope, image=None)
    with pytest.raises(ValueError, match="runtime_image_digest_invalid"):
        DesiredRuntimeSnapshot.build(scope=scope, revision=revision, materialization=materialization, bindings=bindings)
