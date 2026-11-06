"""Unit contracts for the fail-closed resource evidence handoff."""

from prodavan.application.resource_evidence_handoff import (
    HandoffFailureCode,
    PromotionPartition,
    ResourceEvidenceHandoff,
    ResourceEvidenceHandoffService,
    ResourceGrantEvidence,
    ResourceProvenance,
    RuntimeRevisionEvidence,
)
from prodavan.application.resources import ResourceScope, ScopeKind


class Evidence:
    def __init__(self, runtime, grant):
        self.runtime = runtime
        self.grant = grant

    async def read_runtime_evidence(self, *, project_id):
        return self.runtime

    async def read_grant_evidence(self, *, resource_id):
        return self.grant


class Apply:
    def __init__(self):
        self.calls = 0

    async def compare_and_swap_apply(self, *, handoff, expected_generation):
        from prodavan.application.resource_evidence_handoff import ResourceApplyResult

        self.calls += 1
        return ResourceApplyResult(applied=True, cas_generation=expected_generation, evidence_id=handoff.runtime.evidence_id)


def make_handoff(generation=4):
    owner = ResourceScope(ScopeKind.PROJECT, "project-1")
    grant = ResourceGrantEvidence("resource-1", owner, "grant-1", generation)
    return ResourceEvidenceHandoff(
        project_id="project-1",
        runtime=RuntimeRevisionEvidence("project-1", "revision-4", generation, "evidence-1", "digest-1"),
        partition=PromotionPartition("tenant-1", "project-1", "partition-1", "owner-1"),
        grant=grant,
        provenance=(ResourceProvenance("resource-1", "resource-revision-4", "file", "source-digest", "evidence-1"),),
        expected_generation=generation,
        operation_digest="operation-digest",
    )


def test_handoff_contains_owner_grant_partition_revision_generation_and_provenance():
    handoff = make_handoff()
    assert handoff.partition.partition_id == "partition-1"
    assert handoff.grant.owner.id == "project-1"
    assert handoff.runtime.revision_id == "revision-4"
    assert handoff.expected_generation == 4
    assert handoff.provenance[0].evidence_id == "evidence-1"


async def test_apply_fails_closed_without_runtime_revision():
    apply = Apply()
    service = ResourceEvidenceHandoffService(Evidence(None, make_handoff().grant), apply)
    result = await service.apply_handoff(make_handoff())
    assert result.is_fail_closed
    assert HandoffFailureCode.MISSING_RUNTIME_REVISION in {failure.code for failure in result.failures}
    assert apply.calls == 0


async def test_stale_generation_never_reaches_apply():
    apply = Apply()
    handoff = make_handoff(4)
    runtime = RuntimeRevisionEvidence("project-1", "revision-3", 3, "evidence-1", "digest-1")
    service = ResourceEvidenceHandoffService(Evidence(runtime, handoff.grant), apply)
    result = await service.apply_handoff(handoff)
    assert result.is_fail_closed
    assert HandoffFailureCode.GENERATION_STALE in {failure.code for failure in result.failures}
    assert apply.calls == 0


async def test_owner_partition_mismatch_never_reaches_apply():
    apply = Apply()
    handoff = make_handoff()
    wrong_owner = ResourceGrantEvidence(
        "resource-1", ResourceScope(ScopeKind.CABINET, "project-1"), "grant-1", 4
    )
    service = ResourceEvidenceHandoffService(Evidence(handoff.runtime, wrong_owner), apply)
    result = await service.apply_handoff(handoff)
    assert result.is_fail_closed
    assert HandoffFailureCode.PARTITION_MISMATCH in {failure.code for failure in result.failures}
    assert apply.calls == 0


async def test_server_runtime_revision_or_evidence_mismatch_never_reaches_apply():
    apply = Apply()
    handoff = make_handoff()
    runtime = RuntimeRevisionEvidence("project-1", "revision-other", 4, "evidence-other", "digest-1")
    service = ResourceEvidenceHandoffService(Evidence(runtime, handoff.grant), apply)
    result = await service.apply_handoff(handoff)
    assert result.is_fail_closed
    assert HandoffFailureCode.REVISION_MISMATCH in {failure.code for failure in result.failures}
    assert apply.calls == 0


async def test_complete_evidence_uses_one_cas_apply():
    apply = Apply()
    service = ResourceEvidenceHandoffService(Evidence(make_handoff().runtime, make_handoff().grant), apply)
    result = await service.apply_handoff(make_handoff())
    assert result.applied is True
    assert apply.calls == 1
