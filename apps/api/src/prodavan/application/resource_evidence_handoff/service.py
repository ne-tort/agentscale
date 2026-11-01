"""Fail-closed resource owner/grant/revision handoff service."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    HandoffFailure,
    HandoffFailureCode,
    ResourceApplyResult,
    ResourceEvidenceHandoff,
    ResourceGrantEvidence,
    RuntimeRevisionEvidence,
)
from prodavan.application.resources import ScopeKind

from .ports import ResourceApplyPort, ResourceEvidencePort


@dataclass(slots=True)
class ResourceEvidenceHandoffService:
    evidence: ResourceEvidencePort
    apply: ResourceApplyPort

    async def apply_handoff(self, handoff: ResourceEvidenceHandoff) -> ResourceApplyResult:
        """Validate server evidence, then perform exactly one fenced CAS apply."""
        failures: list[HandoffFailure] = []
        runtime = await self.evidence.read_runtime_evidence(project_id=handoff.project_id)
        if not isinstance(runtime, RuntimeRevisionEvidence):
            failures.append(HandoffFailure(HandoffFailureCode.MISSING_RUNTIME_REVISION, "runtime revision evidence is required"))
        else:
            if runtime.project_id != handoff.project_id:
                failures.append(HandoffFailure(HandoffFailureCode.REVISION_MISMATCH, "runtime evidence belongs to another project"))
            if runtime.revision_id.strip() == "":
                failures.append(HandoffFailure(HandoffFailureCode.MISSING_RUNTIME_REVISION, "runtime revision id is empty"))
            if runtime.revision_id != handoff.runtime.revision_id or runtime.evidence_id != handoff.runtime.evidence_id:
                failures.append(HandoffFailure(HandoffFailureCode.REVISION_MISMATCH, "server runtime evidence differs from handoff"))
            if runtime.generation != handoff.expected_generation:
                failures.append(HandoffFailure(HandoffFailureCode.GENERATION_STALE, "runtime generation differs from the expected generation"))
            if not runtime.evidence_id.strip() or not runtime.provenance_digest.strip():
                failures.append(HandoffFailure(HandoffFailureCode.PROVENANCE_MISSING, "runtime provenance evidence is incomplete"))

        grant = await self.evidence.read_grant_evidence(resource_id=handoff.grant.resource_id)
        if not isinstance(grant, ResourceGrantEvidence):
            failures.append(HandoffFailure(HandoffFailureCode.MISSING_EVIDENCE, "resource grant evidence is required", handoff.grant.resource_id))
        else:
            if grant != handoff.grant:
                failures.append(HandoffFailure(HandoffFailureCode.GRANT_MISMATCH, "server grant evidence differs from handoff", grant.resource_id))
            if grant.owner.kind is not ScopeKind.PROJECT or grant.owner.id != handoff.partition.project_id:
                failures.append(HandoffFailure(HandoffFailureCode.PARTITION_MISMATCH, "resource owner is outside the project partition", grant.resource_id))
            if grant.grant_generation != handoff.expected_generation:
                failures.append(HandoffFailure(HandoffFailureCode.GENERATION_STALE, "grant generation differs from runtime generation", grant.resource_id))

        if handoff.grant.owner.id != handoff.partition.project_id:
            failures.append(HandoffFailure(HandoffFailureCode.OWNER_MISMATCH, "handoff owner is outside the project partition", handoff.grant.resource_id))
        if not handoff.provenance:
            failures.append(HandoffFailure(HandoffFailureCode.PROVENANCE_MISSING, "resource provenance evidence is required", handoff.grant.resource_id))
        elif any(
            item.resource_id != handoff.grant.resource_id
            or item.evidence_id != handoff.runtime.evidence_id
            for item in handoff.provenance
        ):
            failures.append(HandoffFailure(HandoffFailureCode.PROVENANCE_MISSING, "provenance identity differs from runtime or grant", handoff.grant.resource_id))

        if failures:
            return ResourceApplyResult(applied=False, failures=tuple(failures), evidence_id=runtime.evidence_id if isinstance(runtime, RuntimeRevisionEvidence) else None)

        result = await self.apply.compare_and_swap_apply(handoff=handoff, expected_generation=handoff.expected_generation)
        if result.applied and result.cas_generation != handoff.expected_generation:
            return ResourceApplyResult(
                applied=False,
                failures=(HandoffFailure(HandoffFailureCode.CAS_CONFLICT, "apply returned an unexpected CAS generation", handoff.grant.resource_id),),
                evidence_id=runtime.evidence_id,
            )
        return result


__all__ = ["ResourceEvidenceHandoffService"]
