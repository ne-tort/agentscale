"""Fail-closed bounded M04E evidence backfill and M05 handoff contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Sequence

from .artifact import ArtifactContentPort
from .readiness import UPSTREAM_BLOCKERS, UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class EvidenceStatus(StrEnum):
    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"
    REPLAY = "replay"
    BLOCKED = "blocked"


class BackfillOutcome(StrEnum):
    ACCEPTED = "accepted"
    REPLAY = "replay"
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


class HandoffStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class SourceRowState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BackfillContext:
    tenant_id: str
    app_definition_id: str
    source_snapshot_id: str
    source_snapshot_digest: str
    source_schema_version: str
    canonical_schema_version: str
    transaction_id: str
    correlation_id: str
    actor: str
    decision_owner: str
    writer_generation: int | None = None

    @property
    def usable(self) -> bool:
        return bool(
            self.tenant_id.strip() and self.app_definition_id.strip()
            and self.source_snapshot_id.strip() and self.source_schema_version.strip()
            and self.canonical_schema_version.strip() and self.transaction_id.strip()
            and self.correlation_id.strip() and self.actor.strip() and self.decision_owner.strip()
            and _DIGEST.fullmatch(self.source_snapshot_digest)
            and (self.writer_generation is None or self.writer_generation >= 0)
        )


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    source_kind: str
    source_id: str
    schema_version: str
    payload: dict[str, object]
    phase: str
    tenant_id: str | None = None
    app_definition_id: str | None = None
    source_state: SourceRowState = SourceRowState.UNKNOWN
    canonical_schema_version: str = "m04e.v1"

    def __post_init__(self) -> None:
        if self.phase not in {"definition", "version"}:
            raise ValueError("phase must be definition or version")
        if not self.source_kind.strip() or not self.source_id.strip() or not self.schema_version.strip():
            raise ValueError("source identity and schema version are required")
        if self.phase == "version" and not self.app_definition_id:
            raise ValueError("version evidence requires its definition identity")
        if not self.canonical_schema_version.strip():
            raise ValueError("canonical schema version is required")

    @property
    def source_digest(self) -> str:
        return _digest({
            "source_kind": self.source_kind, "source_id": self.source_id,
            "schema_version": self.schema_version, "phase": self.phase,
            "tenant_id": self.tenant_id, "app_definition_id": self.app_definition_id,
            "canonical_schema_version": self.canonical_schema_version,
            "source_state": self.source_state.value, "payload": self.payload,
        })

    @property
    def canonical_identity(self) -> tuple[str, ...]:
        if not self.tenant_id or not self.app_definition_id:
            return ()
        if self.phase == "definition":
            app_id = self.payload.get("app_id")
            if not isinstance(app_id, str) or not app_id.strip():
                return ()
            return (self.tenant_id, app_id)
        manifest_version = self.payload.get("manifest_version")
        if not isinstance(manifest_version, str) or not manifest_version.strip():
            return ()
        return (self.tenant_id, self.app_definition_id, manifest_version)

    @property
    def canonical_digest(self) -> str | None:
        if not self.canonical_identity:
            return None
        return _digest({
            "canonical_schema_version": self.canonical_schema_version,
            "phase": self.phase, "identity": self.canonical_identity, "payload": self.payload,
        })


@dataclass(frozen=True, slots=True)
class BackfillCheckpoint:
    partition: tuple[str, str]
    phase: str
    after_source_id: str | None
    sequence: int
    source_snapshot_id: str = ""
    source_schema_version: str = ""
    source_snapshot_digest: str = ""
    canonical_schema_version: str = ""
    writer_generation: int | None = None
    phase_complete: bool = False

    def __post_init__(self) -> None:
        if len(self.partition) != 2 or not all(part.strip() for part in self.partition):
            raise ValueError("checkpoint partition must be (tenant_id, app_definition_id)")
        if self.phase not in {"definition", "version"} or self.sequence < 0:
            raise ValueError("invalid checkpoint phase or sequence")
        if self.writer_generation is not None and self.writer_generation < 0:
            raise ValueError("writer generation cannot be negative")
        if self.phase_complete and self.phase != "version":
            raise ValueError("only the drained version phase completes the definition/version stream")


@dataclass(frozen=True, slots=True)
class BackfillRecord:
    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str
    canonical_digest: str | None
    canonical_identity: tuple[str, ...]
    checkpoint: BackfillCheckpoint
    status: EvidenceStatus
    reason_code: str | None
    correlation_id: str
    tenant_id: str | None = None
    app_definition_id: str | None = None
    phase: str | None = None
    source_snapshot_digest: str | None = None
    proof_evidence: tuple[tuple[str, str], ...] = ()
    artifact_uri: str | None = None
    artifact_digest: str | None = None
    verified_artifact_digest: str | None = None
    current_artifact_digest: str | None = None
    transaction_id: str | None = None
    writer_generation: int | None = None
    outcome: BackfillOutcome | None = None
    observed_source_digest: str | None = None
    observed_artifact_digest: str | None = None
    blocker_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BackfillBatch:
    records: tuple[BackfillRecord, ...]
    checkpoint: BackfillCheckpoint
    complete: bool


@dataclass(frozen=True, slots=True)
class UpstreamGateEvidence:
    tenant_id: str
    app_definition_id: str
    source_snapshot_digest: str
    transaction_id: str
    correlation_id: str
    m04_gate_digest: str
    m04e_gate_digest: str
    active_scope_digest: str

    @property
    def valid(self) -> bool:
        return bool(
            self.tenant_id.strip() and self.app_definition_id.strip()
            and self.transaction_id.strip() and self.correlation_id.strip()
            and all(_DIGEST.fullmatch(value) for value in (
                self.source_snapshot_digest, self.m04_gate_digest,
                self.m04e_gate_digest, self.active_scope_digest,
            ))
        )


@dataclass(frozen=True, slots=True)
class CandidateProof:
    tenant_id: str
    app_definition_id: str
    source_kind: str
    source_id: str
    source_digest: str
    transaction_id: str
    correlation_id: str
    owner_proof_digest: str
    active_scope_proof_digest: str
    canonical_definition_id: str
    app_id: str
    app_version_id: str | None = None
    manifest_version: str | None = None
    artifact_uri: str | None = None
    same_definition_proof_digest: str | None = None
    artifact_digest: str | None = None
    artifact_immutability_proof_digest: str | None = None
    current_version_id: str | None = None
    current_version_definition_id: str | None = None
    current_version_manifest_version: str | None = None
    current_version_artifact_digest: str | None = None
    current_version_artifact_uri: str | None = None
    current_version_lifecycle: str | None = None
    current_version_match_count: int = 0
    current_version_proof_digest: str | None = None
    current_version_same_definition_digest: str | None = None
    current_version_immutable_artifact_digest: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    candidate: EvidenceCandidate
    status: EvidenceStatus
    reason_code: str | None
    proof: CandidateProof | None
    verified_artifact_digest: str | None = None
    verified_current_artifact_digest: str | None = None
    blocker_evidence: tuple[str, ...] = ()


class CandidateReader(Protocol):
    async def read_page(
        self, *, context: BackfillContext, phase: str,
        after_source_id: str | None, limit: int,
    ) -> Sequence[EvidenceCandidate]: ...


class UpstreamGatePort(Protocol):
    async def prove_partition(self, *, context: BackfillContext) -> UpstreamGateEvidence | None:
        """Re-prove retained owner, active typed scope and M04/M04E gates."""
        ...


class CandidateProofPort(Protocol):
    async def prove_candidate(
        self, *, context: BackfillContext, gates: UpstreamGateEvidence,
        candidate: EvidenceCandidate,
    ) -> CandidateProof | None:
        """Prove owner/scope and version same-definition/current/artifact identity."""
        ...


class EvidenceStore(Protocol):
    async def find_existing(
        self, *, context: BackfillContext, source_kind: str, source_id: str,
        source_schema_version: str,
    ) -> BackfillRecord | None:
        """Read first immutable mapping under the exact partition/source key."""
        ...

    async def find_observation(
        self, *, context: BackfillContext, source_kind: str, source_id: str,
        source_schema_version: str, observed_source_digest: str | None,
        observed_artifact_digest: str | None,
    ) -> BackfillRecord | None:
        """Read an exact previously-recorded changed-digest observation."""
        ...

    async def record_blocked(
        self, *, context: BackfillContext, checkpoint: BackfillCheckpoint,
        reason_code: str, blocker_evidence: Sequence[str] = (),
    ) -> BackfillRecord:
        """Append correlated blocked evidence without advancing the checkpoint."""
        ...

    async def classify_and_commit_page(
        self, *, context: BackfillContext, gates: UpstreamGateEvidence,
        expected_checkpoint: BackfillCheckpoint, decisions: Sequence[CandidateDecision],
        page_exhausted: bool,
    ) -> BackfillBatch:
        """Atomically append evidence/audit and CAS the durable cursor.

        Re-derive owner, scope, same-definition and immutable artifact proofs;
        compare snapshot lineage and expected cursor; preserve first mappings;
        replay an exact source and verified-artifact digest without advancing;
        quarantine changed source or artifact digests
        and duplicate tenant-scoped canonical identities across the entire partition.
        Exact quarantine replay retains reason-specific blocker_evidence. A repaired
        quarantine is not promoted by this contract; durable retry semantics are absent.
        Classify every input in order as ACCEPTED, REPLAY, QUARANTINED or CONFLICT,
        retaining the original source identity/digest and adding observed source and
        artifact digests for conflicts. An unchanged source digest with changed
        verified artifact bytes is an immutable artifact conflict, not a replay.
        Persist actionable, redacted blocker evidence for quarantine decisions and
        return that same evidence on an exact quarantine replay.
        Advance cursor and sequence only for non-replay classifications. An exhausted definition
        stream may transition to version; only an exhausted version stream is complete.
        Mapping evidence, quarantine, redacted audit and checkpoint CAS share one
        transaction. Revalidate all upstream and row proof from trusted persistence;
        a proposed ACCEPTED status is never authoritative. No adapter is supplied.
        """
        ...


class DefinitionVersionEvidenceBackfill:
    """Read one bounded page only after trusted gates and durable ports are present."""

    def __init__(
        self, reader: CandidateReader | None, store: EvidenceStore | None, *,
        gates: UpstreamGatePort | None = None, proofs: CandidateProofPort | None = None,
        artifacts: ArtifactContentPort | None = None, page_size: int = 100,
        max_artifact_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if page_size < 1 or page_size > 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if max_artifact_bytes < 1:
            raise ValueError("max_artifact_bytes must be positive")
        self._reader, self._store = reader, store
        self._gates, self._proofs = gates, proofs
        self._artifacts = artifacts
        self._page_size = page_size
        self._max_artifact_bytes = max_artifact_bytes

    async def run_page(self, checkpoint: BackfillCheckpoint, *, context: BackfillContext) -> BackfillBatch:
        if (
            not context.usable
            or checkpoint.partition != (context.tenant_id, context.app_definition_id)
            or checkpoint.source_snapshot_id != context.source_snapshot_id
            or checkpoint.source_schema_version != context.source_schema_version
            or checkpoint.source_snapshot_digest != context.source_snapshot_digest
            or checkpoint.canonical_schema_version != context.canonical_schema_version
            or checkpoint.writer_generation != context.writer_generation
        ):
            return BackfillBatch((), checkpoint, False)
        if self._store is None:
            return BackfillBatch((), checkpoint, False)
        if self._artifacts is None:
            blocked = await self._store.record_blocked(
                context=context, checkpoint=checkpoint, reason_code="artifact_content_port_missing",
                blocker_evidence=("proof-bound artifact content read port is not configured",),
            )
            if blocked.status is not EvidenceStatus.BLOCKED or blocked.checkpoint != checkpoint:
                raise RuntimeError("artifact-port block changed status or checkpoint")
            return BackfillBatch((blocked,), checkpoint, False)
        if self._reader is None or self._gates is None or self._proofs is None:
            blocked = await self._store.record_blocked(
                context=context, checkpoint=checkpoint, reason_code="trusted_proof_or_source_port_missing",
                blocker_evidence=("trusted source, upstream gate, or candidate proof port is not configured",),
            )
            if blocked.status is not EvidenceStatus.BLOCKED or blocked.checkpoint != checkpoint:
                raise RuntimeError("blocked backfill evidence changed status or checkpoint")
            return BackfillBatch((blocked,), checkpoint, False)
        gates = await self._gates.prove_partition(context=context)
        if not UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED or not self._valid_gates(gates, context):
            blockers = UPSTREAM_BLOCKERS if not UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED else (
                "partition gate evidence is absent, malformed, or bound to another lineage",
            )
            blocked = await self._store.record_blocked(
                context=context, checkpoint=checkpoint, reason_code="upstream_owner_scope_gate_unproven",
                blocker_evidence=blockers,
            )
            if blocked.status is not EvidenceStatus.BLOCKED or blocked.checkpoint != checkpoint:
                raise RuntimeError("upstream gate block changed status or checkpoint")
            return BackfillBatch((blocked,), checkpoint, False)
        assert gates is not None

        rows = tuple(await self._reader.read_page(
            context=context, phase=checkpoint.phase,
            after_source_id=checkpoint.after_source_id, limit=self._page_size,
        ))
        if len(rows) > self._page_size or any(row.phase != checkpoint.phase for row in rows):
            raise ValueError("reader exceeded page bound or returned a row from another phase")
        if any(
            row.schema_version != context.source_schema_version
            or row.canonical_schema_version != context.canonical_schema_version
            for row in rows
        ):
            raise ValueError("reader returned a candidate outside checkpoint schema lineage")
        ordered = tuple(sorted(rows, key=lambda row: row.source_id))
        if len({row.source_id for row in ordered}) != len(ordered):
            raise ValueError("reader returned duplicate source identities in one page")
        if checkpoint.after_source_id and any(row.source_id <= checkpoint.after_source_id for row in ordered):
            raise ValueError("reader returned a source identity at or before checkpoint")

        decisions: list[CandidateDecision] = []
        proofless_conflicts: dict[int, str] = {}
        seen: set[tuple[str, ...]] = set()
        for row in ordered:
            reason = self._candidate_rejection(row, context)
            replay_conflict = False
            proof = None if reason else await self._proofs.prove_candidate(
                context=context, gates=gates, candidate=row,
            )
            verified_artifact_digest = None
            verified_current_artifact_digest = None
            if reason is None and not self._valid_candidate_proof(proof, row, context, gates):
                reason, proof = "owner_scope_or_definition_artifact_proof_missing", None
            if reason is None and proof is not None:
                artifact_identity = self._artifact_identity(row, proof, context)
                if artifact_identity is not None:
                    tenant_id, definition_id, version_id, uri, expected_digest = artifact_identity
                    reason, verified_artifact_digest = await self._verify_artifact(
                        tenant_id=tenant_id, app_definition_id=definition_id,
                        app_version_id=version_id, artifact_uri=uri,
                        expected_digest=expected_digest,
                    )
                    if reason is None and proof.current_version_id:
                        if proof.current_version_id == version_id:
                            verified_current_artifact_digest = verified_artifact_digest
                        elif proof.current_version_artifact_uri and proof.current_version_artifact_digest:
                            reason, verified_current_artifact_digest = await self._verify_artifact(
                                tenant_id=context.tenant_id,
                                app_definition_id=context.app_definition_id,
                                app_version_id=proof.current_version_id,
                                artifact_uri=proof.current_version_artifact_uri,
                                expected_digest=proof.current_version_artifact_digest,
                            )
                            if reason is not None:
                                reason = "current_" + reason
                        else:
                            reason = "current_version_artifact_proof_missing"
            if reason is None and proof is not None and proof.current_version_id and verified_current_artifact_digest is None:
                reason = "current_version_artifact_content_missing"
            existing = await self._store.find_existing(
                context=context,
                source_kind=row.source_kind,
                source_id=row.source_id,
                source_schema_version=row.schema_version,
            )
            recorded_source_conflict = None
            recorded_artifact_conflict = None
            if existing is not None and existing.source_digest != row.source_digest:
                recorded_source_conflict = await self._store.find_observation(
                    context=context,
                    source_kind=row.source_kind,
                    source_id=row.source_id,
                    source_schema_version=row.schema_version,
                    observed_source_digest=row.source_digest,
                    observed_artifact_digest=None,
                )
            if (
                existing is not None
                and existing.source_digest == row.source_digest
                and existing.verified_artifact_digest is not None
                and verified_artifact_digest is not None
                and existing.verified_artifact_digest != verified_artifact_digest
            ):
                recorded_artifact_conflict = await self._store.find_observation(
                    context=context,
                    source_kind=row.source_kind,
                    source_id=row.source_id,
                    source_schema_version=row.schema_version,
                    observed_source_digest=None,
                    observed_artifact_digest=verified_artifact_digest,
                )
            if existing is not None and existing.source_digest != row.source_digest:
                status = EvidenceStatus.REPLAY if recorded_source_conflict is not None else EvidenceStatus.CONFLICT
                reason = "source_digest_changed"
                proof = None
                replay_conflict = recorded_source_conflict is not None
                if recorded_source_conflict is None:
                    proofless_conflicts[len(decisions)] = row.source_digest
            elif (
                existing is not None
                and existing.status is EvidenceStatus.CONFLICT
                and existing.source_digest == row.source_digest
                and existing.verified_artifact_digest == verified_artifact_digest
            ):
                status = EvidenceStatus.REPLAY
                reason = existing.reason_code
                proof = None
                replay_conflict = True
            elif (
                existing is not None
                and existing.verified_artifact_digest is not None
                and verified_artifact_digest is not None
                and existing.verified_artifact_digest != verified_artifact_digest
            ):
                status = EvidenceStatus.REPLAY if recorded_artifact_conflict is not None else EvidenceStatus.CONFLICT
                reason = "artifact_digest_changed"
                proof = None
                replay_conflict = recorded_artifact_conflict is not None
                if recorded_artifact_conflict is None:
                    proofless_conflicts[len(decisions)] = verified_artifact_digest
            status = (
                EvidenceStatus.REPLAY if replay_conflict
                else EvidenceStatus.ACCEPTED if reason is None
                else EvidenceStatus.QUARANTINED
            )
            if len(decisions) in proofless_conflicts:
                status = EvidenceStatus.CONFLICT
            elif not replay_conflict and reason in {"source_digest_changed", "artifact_digest_changed"}:
                status = EvidenceStatus.CONFLICT
            identity = row.canonical_identity
            if status is EvidenceStatus.ACCEPTED and identity in seen:
                status, reason, proof = EvidenceStatus.CONFLICT, "duplicate_canonical_identity", None
            if identity:
                seen.add(identity)
            blocker_evidence = (
                self._quarantine_guidance(reason)
                if status is EvidenceStatus.QUARANTINED else ()
            )
            decisions.append(CandidateDecision(
                row, status, reason, proof, verified_artifact_digest,
                verified_current_artifact_digest, blocker_evidence,
            ))

        exhausted = len(rows) < self._page_size
        complete = exhausted and checkpoint.phase == "version"
        committed = await self._store.classify_and_commit_page(
            context=context, gates=gates, expected_checkpoint=checkpoint,
            decisions=tuple(decisions), page_exhausted=exhausted,
        )
        if committed.checkpoint.partition != checkpoint.partition:
            raise RuntimeError("evidence store returned checkpoint for another partition")
        if len(committed.records) != len(decisions):
            raise RuntimeError("evidence store did not return one classification per source candidate")
        for decision_index, (decision, record) in enumerate(
            zip(decisions, committed.records, strict=True)
        ):
            if (
                (record.source_kind, record.source_id, record.schema_version)
                != (decision.candidate.source_kind, decision.candidate.source_id,
                    decision.candidate.schema_version)
                or record.outcome not in {
                    BackfillOutcome.ACCEPTED, BackfillOutcome.REPLAY,
                    BackfillOutcome.QUARANTINED, BackfillOutcome.CONFLICT,
                }
            ):
                raise RuntimeError("evidence store classification does not match its source candidate")
            if decision.reason_code in {"source_digest_changed", "artifact_digest_changed"}:
                expected_outcome = (
                    BackfillOutcome.REPLAY if decision.status is EvidenceStatus.REPLAY
                    else BackfillOutcome.CONFLICT
                )
                if record.outcome is not expected_outcome or record.status is not EvidenceStatus.CONFLICT:
                    raise RuntimeError("durable store must return an immutable source/artifact conflict")
                if decision.status is EvidenceStatus.REPLAY:
                    if record.reason_code != decision.reason_code:
                        raise RuntimeError("conflict replay returned a different immutable reason")
                    expected_observation = (
                        decision.candidate.source_digest if decision.reason_code == "source_digest_changed"
                        else decision.verified_artifact_digest
                    )
                    observed = (
                        record.observed_source_digest if decision.reason_code == "source_digest_changed"
                        else record.observed_artifact_digest
                    )
                    if observed != expected_observation:
                        raise RuntimeError("conflict replay did not return the exact prior digest observation")
                if decision.status is EvidenceStatus.CONFLICT:
                    expected_observation = proofless_conflicts.get(decision_index)
                    observed = (
                        record.observed_source_digest if decision.reason_code == "source_digest_changed"
                        else record.observed_artifact_digest
                    )
                    if expected_observation is not None and observed != expected_observation:
                        raise RuntimeError("durable conflict omitted the changed source/artifact digest")
            elif decision.status is EvidenceStatus.CONFLICT and (
                record.outcome is not BackfillOutcome.CONFLICT
                or record.source_digest != decision.candidate.source_digest
                or record.reason_code != decision.reason_code
            ):
                raise RuntimeError("canonical-identity conflict did not retain source evidence")
            if record.outcome is BackfillOutcome.CONFLICT:
                changed_digest_conflict = decision.reason_code in {
                    "source_digest_changed", "artifact_digest_changed",
                }
                source_conflict = record.observed_source_digest == decision.candidate.source_digest
                artifact_conflict = (
                    record.observed_artifact_digest == decision.verified_artifact_digest
                    and decision.verified_artifact_digest is not None
                )
                if (
                    record.status is not EvidenceStatus.CONFLICT
                    or not _DIGEST.fullmatch(record.source_digest)
                    or (changed_digest_conflict and not (source_conflict or artifact_conflict))
                    or (not changed_digest_conflict and record.reason_code != decision.reason_code)
                ):
                    raise RuntimeError("conflict must retain original identity and digest the changed observation")
            elif record.source_digest != decision.candidate.source_digest:
                if not (
                    record.outcome is BackfillOutcome.REPLAY
                    and record.status is EvidenceStatus.CONFLICT
                    and record.observed_source_digest == decision.candidate.source_digest
                ):
                    raise RuntimeError("evidence store changed the source digest outside a conflict replay")
            elif record.reason_code != decision.reason_code:
                raise RuntimeError("evidence store returned a different classification reason")
            digest_conflict_replay = (
                record.outcome is BackfillOutcome.REPLAY
                and record.status is EvidenceStatus.CONFLICT
            )
            if record.outcome is not BackfillOutcome.CONFLICT and not digest_conflict_replay and (
                record.canonical_identity != decision.candidate.canonical_identity
            ):
                raise RuntimeError("evidence store returned a different canonical identity")
            if record.status is EvidenceStatus.ACCEPTED:
                if record.canonical_digest != decision.candidate.canonical_digest:
                    raise RuntimeError("evidence store returned a different canonical payload digest")
                if decision.proof is None:
                    raise RuntimeError("evidence store accepted a candidate without trusted proof")
                expected_proofs = {
                    "owner": decision.proof.owner_proof_digest,
                    "active_scope": decision.proof.active_scope_proof_digest,
                }
                if decision.candidate.phase == "version":
                    expected_proofs.update({
                        "same_definition": decision.proof.same_definition_proof_digest or "",
                        "immutable_artifact": decision.proof.artifact_immutability_proof_digest or "",
                        "artifact_content": decision.verified_artifact_digest or "",
                    })
                if decision.candidate.phase == "definition" and decision.proof.current_version_id:
                    expected_proofs["current_version"] = decision.proof.current_version_proof_digest or ""
                    expected_proofs["current_same_definition"] = decision.proof.current_version_same_definition_digest or ""
                    expected_proofs["current_artifact_immutability"] = decision.proof.current_version_immutable_artifact_digest or ""
                    expected_proofs["current_artifact_content"] = decision.verified_current_artifact_digest or ""
                if decision.candidate.phase == "version" and decision.proof.current_version_id:
                    expected_proofs["current_version"] = decision.proof.current_version_proof_digest or ""
                    expected_proofs["current_same_definition"] = decision.proof.current_version_same_definition_digest or ""
                    expected_proofs["current_artifact_immutability"] = decision.proof.current_version_immutable_artifact_digest or ""
                    expected_proofs["current_artifact_content"] = decision.verified_current_artifact_digest or ""
                if record.outcome is not BackfillOutcome.REPLAY and dict(record.proof_evidence) != expected_proofs:
                    raise RuntimeError("evidence store returned proof digests different from the trusted candidate proof")
                if decision.candidate.phase == "version" and (
                    record.artifact_digest != decision.proof.artifact_digest
                    or record.artifact_uri != decision.proof.artifact_uri
                    or record.verified_artifact_digest != decision.verified_artifact_digest
                ):
                    raise RuntimeError("evidence store returned different immutable artifact identity")
                if decision.proof.current_version_id and (
                    record.current_artifact_digest != decision.verified_current_artifact_digest
                ):
                    raise RuntimeError("evidence store returned different current-version artifact bytes")
            elif record.canonical_digest is not None:
                raise RuntimeError("quarantined or conflicted evidence cannot expose a canonical digest")
            if record.outcome is BackfillOutcome.REPLAY and record.status is EvidenceStatus.QUARANTINED:
                if record.reason_code != decision.reason_code:
                    raise RuntimeError("quarantine replay returned a different immutable reason")
            if record.outcome is BackfillOutcome.QUARANTINED and record.status is not EvidenceStatus.QUARANTINED:
                raise RuntimeError("quarantine outcome does not retain quarantined evidence")
            if (
                record.outcome is BackfillOutcome.QUARANTINED
                or (record.outcome is BackfillOutcome.REPLAY
                    and record.status is EvidenceStatus.QUARANTINED)
            ) and record.blocker_evidence != decision.blocker_evidence:
                raise RuntimeError("quarantine evidence omitted or changed actionable blocker guidance")
        all_replays = bool(committed.records) and all(
            record.outcome is BackfillOutcome.REPLAY for record in committed.records
        )
        if all_replays:
            self._validate_replay_checkpoint(checkpoint, committed.checkpoint)
            if committed.complete != committed.checkpoint.phase_complete:
                raise RuntimeError("replay returned an inconsistent durable completion marker")
            return committed
        consumed = tuple(
            record for record in committed.records
            if record.outcome is not BackfillOutcome.REPLAY
        )
        expected_phase = checkpoint.phase
        expected_after = checkpoint.after_source_id
        expected_sequence = checkpoint.sequence + len(consumed)
        expected_complete = False
        if exhausted and not all_replays:
            if checkpoint.phase == "definition":
                if not committed.records or (
                    consumed and consumed[-1].source_id == ordered[-1].source_id
                ):
                    expected_phase, expected_after = "version", None
                    expected_sequence += 1
            elif not committed.records or (
                consumed and consumed[-1].source_id == ordered[-1].source_id
            ):
                expected_after = consumed[-1].source_id if consumed else checkpoint.after_source_id
                expected_complete = True
        if consumed and expected_phase == checkpoint.phase:
            expected_after = consumed[-1].source_id
        expected_checkpoint = BackfillCheckpoint(
            partition=checkpoint.partition, phase=expected_phase, after_source_id=expected_after,
            sequence=expected_sequence, source_snapshot_id=checkpoint.source_snapshot_id,
            source_schema_version=checkpoint.source_schema_version,
            source_snapshot_digest=checkpoint.source_snapshot_digest,
            canonical_schema_version=checkpoint.canonical_schema_version,
            writer_generation=checkpoint.writer_generation, phase_complete=expected_complete,
        )
        if committed.checkpoint != expected_checkpoint:
            raise RuntimeError("evidence store violated durable checkpoint/replay semantics")
        if committed.complete != expected_complete:
            raise RuntimeError("evidence store returned an inconsistent completion marker")
        if any(
            record.outcome is not BackfillOutcome.REPLAY
            and record.correlation_id != context.correlation_id
            for record in committed.records
        ):
            raise RuntimeError("evidence store returned a different correlation ID")
        if any(
            record.tenant_id != context.tenant_id
            or record.app_definition_id != context.app_definition_id
            or record.source_snapshot_digest != context.source_snapshot_digest
            or record.phase != checkpoint.phase
            or record.schema_version != context.source_schema_version
            or record.writer_generation != context.writer_generation
            for record in committed.records
        ):
            raise RuntimeError("evidence store returned a row outside the requested partition or lineage")
        if any(record.outcome is BackfillOutcome.ACCEPTED and record.status is not EvidenceStatus.ACCEPTED
               for record in committed.records):
            raise RuntimeError("evidence store accepted outcome does not retain accepted evidence")
        if any(record.outcome is BackfillOutcome.QUARANTINED and record.status is not EvidenceStatus.QUARANTINED
               for record in committed.records):
            raise RuntimeError("evidence store quarantine outcome has a mismatched evidence status")
        if any(record.status is EvidenceStatus.ACCEPTED and not self._record_has_positive_proof(record)
               for record in committed.records):
            raise RuntimeError("evidence store accepted a row without required proof digests")
        if any(
            record.outcome is BackfillOutcome.REPLAY
            and record.status is EvidenceStatus.CONFLICT
            and record.reason_code not in {"source_digest_changed", "artifact_digest_changed"}
            for record in committed.records
        ):
            raise RuntimeError("replayed conflict must retain a changed-digest reason")
        return committed

    @staticmethod
    def _validate_replay_checkpoint(
        requested: BackfillCheckpoint, durable: BackfillCheckpoint,
    ) -> None:
        if (
            durable.partition != requested.partition
            or durable.source_snapshot_id != requested.source_snapshot_id
            or durable.source_schema_version != requested.source_schema_version
            or durable.source_snapshot_digest != requested.source_snapshot_digest
            or durable.canonical_schema_version != requested.canonical_schema_version
            or durable.writer_generation != requested.writer_generation
            or durable.sequence < requested.sequence
        ):
            raise RuntimeError("exact replay returned an unrelated or regressed durable checkpoint")
        if (
            durable.phase == requested.phase
            and durable.sequence == requested.sequence
            and durable.after_source_id != requested.after_source_id
        ):
            raise RuntimeError("exact replay checkpoint changed its cursor without advancing sequence")
        phase_order = {"definition": 0, "version": 1}
        if phase_order[durable.phase] < phase_order[requested.phase]:
            raise RuntimeError("exact replay returned a checkpoint from an earlier phase")
        if durable.phase == requested.phase and durable.sequence > requested.sequence:
            if (
                durable.after_source_id is None
                or (requested.after_source_id is not None and durable.after_source_id <= requested.after_source_id)
            ):
                raise RuntimeError("replay checkpoint advanced without consuming source identity")
        if durable.phase_complete and durable.phase != "version":
            raise RuntimeError("only version phase can be complete")

    @staticmethod
    def _valid_gates(gates: UpstreamGateEvidence | None, context: BackfillContext) -> bool:
        return bool(
            gates and gates.valid and gates.tenant_id == context.tenant_id
            and gates.app_definition_id == context.app_definition_id
            and gates.source_snapshot_digest == context.source_snapshot_digest
            and gates.transaction_id == context.transaction_id
            and gates.correlation_id == context.correlation_id
        )

    @staticmethod
    def _quarantine_guidance(reason_code: str | None) -> tuple[str, ...]:
        guidance = {
            "artifact_content_missing": (
                "Restore and review the proof-bound artifact content. No durable quarantine-retry operation is defined; preserve this evidence and request an approved retry path.",
            ),
            "artifact_content_exceeds_limit": (
                "Review the artifact size against the configured limit; do not replace an immutable artifact in place.",
            ),
            "artifact_content_digest_mismatch": (
                "Compare the source digest with retrieved bytes and resolve the artifact lineage; do not retry until the mismatch is reviewed.",
            ),
            "archived_source_row": (
                "Confirm the source lifecycle; archived rows remain quarantined unless an approved snapshot resolves them.",
            ),
            "source_lifecycle_unproven": (
                "Re-establish active source lifecycle evidence, then retry from the unchanged durable checkpoint.",
            ),
            "missing_or_invalid_artifact_digest": (
                "Provide a valid source artifact digest through an approved source correction; retain this evidence.",
            ),
            "missing_artifact_reference": (
                "Restore the source artifact reference and its proof before retrying this unchanged source identity.",
            ),
        }
        return guidance.get(reason_code or "", (
            "Review the quarantine reason and repair its trusted source or proof dependency before retrying.",
        ))

    @staticmethod
    def _candidate_rejection(row: EvidenceCandidate, context: BackfillContext) -> str | None:
        if row.tenant_id != context.tenant_id or row.app_definition_id != context.app_definition_id:
            return "tenant_or_definition_mismatch"
        if row.source_state is SourceRowState.ARCHIVED:
            return "archived_source_row"
        if row.source_state is SourceRowState.MISSING:
            return "missing_source_row"
        if row.source_state is not SourceRowState.ACTIVE:
            return "source_lifecycle_unproven"
        if not row.canonical_identity:
            return "canonical_identity_missing"
        if row.payload.get("status") not in {"draft", "published"}:
            return "archived_or_disallowed_lifecycle"
        if row.phase == "version":
            artifact = row.payload.get("artifact_digest")
            if not isinstance(artifact, str) or not _DIGEST.fullmatch(artifact):
                return "missing_or_invalid_artifact_digest"
            artifact_uri = row.payload.get("artifact_uri")
            if not isinstance(artifact_uri, str) or not artifact_uri.strip():
                return "missing_artifact_reference"
            manifest_version = row.payload.get("manifest_version")
            if not isinstance(manifest_version, str) or not manifest_version.strip():
                return "manifest_version_missing"
            manifest = row.payload.get("manifest")
            if not isinstance(manifest, dict):
                return "version_manifest_missing"
            if (
                manifest.get("appId") != DefinitionVersionEvidenceBackfill._candidate_app_id(row)
                or manifest.get("version") != manifest_version
            ):
                return "version_manifest_identity_mismatch"
            if row.payload.get("status") == "published" and not row.payload.get("definition_current_version_id"):
                return "published_version_current_link_missing"


        elif row.payload.get("status") == "published" and not row.payload.get("current_version_id"):
            return "published_definition_current_version_missing"
        return None

    @staticmethod
    def _valid_candidate_proof(
        proof: CandidateProof | None, row: EvidenceCandidate,
        context: BackfillContext, gates: UpstreamGateEvidence,
    ) -> bool:
        if proof is None or (
            proof.tenant_id != context.tenant_id
            or proof.app_definition_id != context.app_definition_id
            or proof.canonical_definition_id != context.app_definition_id
            or proof.source_kind != row.source_kind
            or proof.source_id != row.source_id
            or proof.app_id != DefinitionVersionEvidenceBackfill._candidate_app_id(row)
            or proof.source_digest != row.source_digest
            or proof.transaction_id != context.transaction_id
            or proof.correlation_id != context.correlation_id
            or not _DIGEST.fullmatch(proof.owner_proof_digest)
            or not _DIGEST.fullmatch(proof.active_scope_proof_digest)
            or proof.active_scope_proof_digest != gates.active_scope_digest
        ):
            return False
        if proof.current_version_match_count != (1 if proof.current_version_id else 0):
            return False
        if proof.current_version_id and (
            proof.current_version_definition_id != context.app_definition_id
            or not proof.current_version_proof_digest
            or not _DIGEST.fullmatch(proof.current_version_proof_digest)
            or not proof.current_version_manifest_version
            or not proof.current_version_artifact_uri
            or not proof.current_version_artifact_digest
            or not _DIGEST.fullmatch(proof.current_version_artifact_digest)
            or proof.current_version_lifecycle not in {"draft", "published"}
            or not proof.current_version_same_definition_digest
            or not _DIGEST.fullmatch(proof.current_version_same_definition_digest)
            or not proof.current_version_immutable_artifact_digest
            or not _DIGEST.fullmatch(proof.current_version_immutable_artifact_digest)
        ):
            return False
        current_id = (
            row.payload.get("current_version_id") if row.phase == "definition"
            else row.payload.get("definition_current_version_id")
        )
        if current_id and (
            proof.current_version_id != current_id
            or proof.current_version_definition_id != context.app_definition_id
            or not proof.current_version_proof_digest
            or not _DIGEST.fullmatch(proof.current_version_proof_digest)
            or not proof.current_version_manifest_version
            or not proof.current_version_artifact_digest
            or not _DIGEST.fullmatch(proof.current_version_artifact_digest)
            or not proof.current_version_artifact_uri
            or proof.current_version_lifecycle not in {"draft", "published"}
            or (row.payload.get("status") == "published" and proof.current_version_lifecycle != "published")
        ):
            return False
        if row.phase == "version":
            artifact = row.payload.get("artifact_digest")
            if (
                not proof.app_version_id
                or proof.manifest_version != row.payload.get("manifest_version")
                or proof.artifact_uri != row.payload.get("artifact_uri")
                or not proof.same_definition_proof_digest
                or not _DIGEST.fullmatch(proof.same_definition_proof_digest)
                or proof.artifact_digest != artifact
                or not proof.artifact_immutability_proof_digest
                or not _DIGEST.fullmatch(proof.artifact_immutability_proof_digest)
            ):
                return False
            if row.payload.get("status") == "published" and (
                not proof.current_version_id
                or proof.current_version_definition_id != context.app_definition_id
                or not proof.current_version_proof_digest
                or not _DIGEST.fullmatch(proof.current_version_proof_digest)
                or not proof.current_version_manifest_version
                or not proof.current_version_artifact_digest
                or not _DIGEST.fullmatch(proof.current_version_artifact_digest)
                or proof.current_version_lifecycle != "published"
                or not proof.current_version_same_definition_digest
                or not _DIGEST.fullmatch(proof.current_version_same_definition_digest)
                or not proof.current_version_immutable_artifact_digest
                or not _DIGEST.fullmatch(proof.current_version_immutable_artifact_digest)
            ):
                return False
            if proof.current_version_id == proof.app_version_id and (
                proof.current_version_manifest_version != row.payload.get("manifest_version")
                or proof.current_version_artifact_digest != artifact
                or proof.current_version_artifact_uri != proof.artifact_uri
            ):
                return False
            if row.payload.get("status") == "draft" and proof.current_version_id is not None:
                return False
            if row.payload.get("status") != "published" and proof.current_version_id is not None:
                if (
                    proof.current_version_definition_id != context.app_definition_id
                    or proof.current_version_lifecycle not in {"draft", "published"}
                    or not proof.current_version_same_definition_digest
                    or not _DIGEST.fullmatch(proof.current_version_same_definition_digest)
                    or not proof.current_version_immutable_artifact_digest
                    or not _DIGEST.fullmatch(proof.current_version_immutable_artifact_digest)
                ):
                    return False
        return True

    @staticmethod
    def _candidate_app_id(row: EvidenceCandidate) -> object:
        if row.phase == "definition":
            app_id = row.payload.get("app_id")
            return app_id if isinstance(app_id, str) else None
        manifest = row.payload.get("manifest")
        app_id = manifest.get("appId") if isinstance(manifest, dict) else None
        return app_id if isinstance(app_id, str) else None

    @staticmethod
    def _artifact_identity(
        row: EvidenceCandidate, proof: CandidateProof, context: BackfillContext,
    ) -> tuple[str, str, str, str, str] | None:
        if row.phase == "version":
            if not proof.app_version_id or not proof.artifact_uri or not proof.artifact_digest:
                return None
            return (
                context.tenant_id, context.app_definition_id, proof.app_version_id,
                proof.artifact_uri, proof.artifact_digest,
            )
        if proof.current_version_id:
            if (
                not proof.current_version_artifact_uri
                or not proof.current_version_artifact_digest
            ):
                return None
            return (
                context.tenant_id, context.app_definition_id, proof.current_version_id,
                proof.current_version_artifact_uri, proof.current_version_artifact_digest,
            )
        return None

    async def _verify_artifact(
        self, *, tenant_id: str, app_definition_id: str, app_version_id: str,
        artifact_uri: str, expected_digest: str,
    ) -> tuple[str | None, str | None]:
        assert self._artifacts is not None
        content = await self._artifacts.read_artifact(
            tenant_id=tenant_id, app_definition_id=app_definition_id,
            app_version_id=app_version_id, artifact_uri=artifact_uri,
            max_bytes=self._max_artifact_bytes,
        )
        if content is None:
            return "artifact_content_missing", None
        if not isinstance(content, bytes):
            raise TypeError("artifact content port must return bytes or None")
        if len(content) > self._max_artifact_bytes:
            return "artifact_content_exceeds_limit", None
        actual_digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if actual_digest != expected_digest:
            return "artifact_content_digest_mismatch", actual_digest
        return None, actual_digest

    @staticmethod
    def _record_has_positive_proof(record: BackfillRecord) -> bool:
        evidence = dict(record.proof_evidence)
        required = {"owner", "active_scope"}
        if record.phase == "version":
            required.update({"same_definition", "immutable_artifact", "artifact_content"})
            if (
                len(record.canonical_identity) != 3
                or record.canonical_identity[:2] != (record.tenant_id, record.app_definition_id)
                or not record.artifact_digest
                or not _DIGEST.fullmatch(record.artifact_digest)
            ):
                return False
        if record.phase == "definition" and (
            len(record.canonical_identity) != 2 or record.canonical_identity[0] != record.tenant_id
        ):
            return False
        if record.phase == "definition" and dict(record.proof_evidence).get("current_version"):
            required.update({
                "current_version", "current_artifact_content",
                "current_same_definition", "current_artifact_immutability",
            })
        if record.phase == "version" and dict(record.proof_evidence).get("current_version"):
            required.update({
                "current_version", "current_artifact_content",
                "current_same_definition", "current_artifact_immutability",
            })
        return all(_DIGEST.fullmatch(evidence.get(key, "")) for key in required)


@dataclass(frozen=True, slots=True)
class HandoffGate:
    name: str
    passed: bool
    evidence_digest: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class M05HandoffMarker:
    record_id: str
    task_id: str
    status: HandoffStatus
    partition: tuple[str, str]
    source_snapshot_digest: str
    canonical_digest: str
    checkpoint: BackfillCheckpoint
    accepted_count: int
    quarantined_count: int
    gate_evidence: tuple[HandoffGate, ...]
    actor: str
    recorded_at: str
    decision_owner: str
    writer_generation: int | None
    correlation_id: str
    canonical_positive_writes: bool = False


@dataclass(frozen=True, slots=True)
class M05EvidenceExport:
    tenant_id: str
    app_definition_id: str
    source_snapshot_digest: str
    canonical_digest: str
    checkpoint: BackfillCheckpoint
    accepted: tuple[BackfillRecord, ...]
    quarantined: tuple[BackfillRecord, ...]
    gates: tuple[HandoffGate, ...]
    status: HandoffStatus
    canonical_positive_writes: bool = False
    handoff_marker: M05HandoffMarker | None = None


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    record_id: str
    task_id: str
    status: str
    partition: tuple[str, str]
    source_snapshot_digest: str
    canonical_digest: str
    checkpoint: BackfillCheckpoint
    accepted_count: int
    quarantined_count: int
    gate_evidence: tuple[HandoffGate, ...]
    actor: str
    recorded_at: str
    decision_owner: str
    writer_generation: int | None
    correlation_id: str
    m05_handoff: HandoffStatus
    legacy_read_write_owner: str = "legacy"
    canonical_positive_writes: bool = False


class M05HandoffPort(Protocol):
    async def append_marker(self, marker: M05HandoffMarker) -> M05HandoffMarker:
        """Append immutable marker; exact retry returns the original marker."""
        ...


async def persist_m05_handoff(
    evidence: M05EvidenceExport, *, store: M05HandoffPort | None,
    actor: str, recorded_at: str, decision_owner: str,
    correlation_id: str, writer_generation: int | None,
) -> M05EvidenceExport:
    if store is None:
        raise RuntimeError("durable M05 handoff port is not configured")
    if not all(value.strip() for value in (actor, recorded_at, decision_owner, correlation_id)):
        raise ValueError("handoff actor, time, decision owner and correlation are required")
    if evidence.checkpoint.partition != (evidence.tenant_id, evidence.app_definition_id):
        raise ValueError("handoff checkpoint does not match its partition")
    if not UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED and evidence.status is HandoffStatus.READY:
        raise ValueError("M05 handoff cannot be ready while upstream owner/scope gates are closed")
    if evidence.canonical_positive_writes:
        raise ValueError("M05 evidence handoff cannot enable canonical positive writes")
    if (
        evidence.checkpoint.phase != "version"
        or not evidence.checkpoint.phase_complete
        or evidence.checkpoint.source_snapshot_digest != evidence.source_snapshot_digest
        or not _DIGEST.fullmatch(evidence.source_snapshot_digest)
        or not _DIGEST.fullmatch(evidence.canonical_digest)
    ):
        raise ValueError("M05 evidence handoff requires a complete digest-bound version checkpoint")
    recomputed = export_m05_evidence(
        tenant_id=evidence.tenant_id,
        app_definition_id=evidence.app_definition_id,
        records=(*evidence.accepted, *evidence.quarantined),
        source_snapshot_digest=evidence.source_snapshot_digest,
        checkpoint=evidence.checkpoint,
        gates=evidence.gates,
    )
    if (
        recomputed.status is not evidence.status
        or recomputed.canonical_digest != evidence.canonical_digest
        or recomputed.accepted != evidence.accepted
        or recomputed.quarantined != evidence.quarantined
    ):
        raise ValueError("M05 evidence export does not match its scoped records and gate evidence")
    record_id = _digest({
        "task_id": "MIG-M04E-02", "partition": evidence.checkpoint.partition,
        "source_snapshot_digest": evidence.source_snapshot_digest,
        "canonical_digest": evidence.canonical_digest,
        "checkpoint": {
            "phase": evidence.checkpoint.phase,
            "after_source_id": evidence.checkpoint.after_source_id,
            "sequence": evidence.checkpoint.sequence,
            "source_snapshot_id": evidence.checkpoint.source_snapshot_id,
            "source_schema_version": evidence.checkpoint.source_schema_version,
            "source_snapshot_digest": evidence.checkpoint.source_snapshot_digest,
            "canonical_schema_version": evidence.checkpoint.canonical_schema_version,
            "writer_generation": evidence.checkpoint.writer_generation,
        },
        "correlation_id": correlation_id,
        "actor": actor,
        "recorded_at": recorded_at,
        "decision_owner": decision_owner,
        "writer_generation": writer_generation,
        "status": evidence.status.value,
    })
    marker = M05HandoffMarker(
        record_id=record_id, task_id="MIG-M04E-02", status=evidence.status,
        partition=evidence.checkpoint.partition,
        source_snapshot_digest=evidence.source_snapshot_digest,
        canonical_digest=evidence.canonical_digest, checkpoint=evidence.checkpoint,
        accepted_count=len(evidence.accepted), quarantined_count=len(evidence.quarantined),
        gate_evidence=evidence.gates, actor=actor, recorded_at=recorded_at,
        decision_owner=decision_owner, writer_generation=writer_generation,
        correlation_id=correlation_id, canonical_positive_writes=False,
    )
    persisted = await store.append_marker(marker)
    if persisted != marker:
        raise RuntimeError("handoff store returned a different immutable marker")
    return M05EvidenceExport(
        tenant_id=evidence.tenant_id, app_definition_id=evidence.app_definition_id,
        source_snapshot_digest=evidence.source_snapshot_digest,
        canonical_digest=evidence.canonical_digest, checkpoint=evidence.checkpoint,
        accepted=evidence.accepted, quarantined=evidence.quarantined, gates=evidence.gates,
        status=evidence.status, canonical_positive_writes=False, handoff_marker=persisted,
    )


def export_m05_evidence(
    *, tenant_id: str, app_definition_id: str, records: Sequence[BackfillRecord],
    source_snapshot_digest: str, checkpoint: BackfillCheckpoint,
    gates: Sequence[HandoffGate],
) -> M05EvidenceExport:
    partition = (tenant_id, app_definition_id)
    scoped = tuple(
        row for row in records
        if row.tenant_id == tenant_id and row.app_definition_id == app_definition_id
        and row.checkpoint.partition == partition
    )
    accepted = tuple(row for row in scoped if row.status is EvidenceStatus.ACCEPTED)
    quarantined = tuple(row for row in scoped if row.status is not EvidenceStatus.ACCEPTED)
    all_gates = tuple(gates)
    if not UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED:
        all_gates += (HandoffGate(
            name="upstream_owner_scope_gate", passed=False,
            reason="; ".join(UPSTREAM_BLOCKERS),
        ),)
    required = {
        "MIG-M04-01", "MIG-M04-02", "MIG-M04E-01", "active_typed_parent_scope",
        "same_definition_version", "immutable_artifact",
    }
    by_name = {gate.name: gate for gate in all_gates}
    gate_ready = required.issubset(by_name) and all(
        by_name[name].passed and by_name[name].evidence_digest is not None
        and _DIGEST.fullmatch(by_name[name].evidence_digest)
        for name in required
    )
    lineage_ready = bool(
        checkpoint.partition == partition and checkpoint.phase == "version"
        and checkpoint.phase_complete
        and checkpoint.source_snapshot_digest == source_snapshot_digest
        and _DIGEST.fullmatch(source_snapshot_digest)
    )
    proofs_ready = bool(accepted) and all(
        DefinitionVersionEvidenceBackfill._record_has_positive_proof(row)
        and row.source_snapshot_digest == source_snapshot_digest
        and row.transaction_id and row.correlation_id
        for row in accepted
    )
    canonical_digest = _digest(tuple(sorted(
        (row.source_id, row.canonical_identity, row.canonical_digest) for row in accepted
    )))
    ready = bool(
        UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED
        and scoped and not quarantined and gate_ready and lineage_ready and proofs_ready
    )
    return M05EvidenceExport(
        tenant_id=tenant_id, app_definition_id=app_definition_id,
        source_snapshot_digest=source_snapshot_digest, canonical_digest=canonical_digest,
        checkpoint=checkpoint, accepted=accepted, quarantined=quarantined, gates=all_gates,
        status=HandoffStatus.READY if ready else HandoffStatus.BLOCKED,
        canonical_positive_writes=False,
    )


__all__ = [
    "BackfillBatch", "BackfillCheckpoint", "BackfillContext", "BackfillOutcome",
    "BackfillRecord", "CandidateDecision", "CandidateProof", "CandidateProofPort",
    "CandidateReader", "DefinitionVersionEvidenceBackfill", "EvidenceCandidate",
    "EvidenceStatus", "EvidenceStore", "HandoffGate", "HandoffStatus",
    "M05EvidenceExport", "M05HandoffMarker", "M05HandoffPort", "SourceRowState",
    "PromotionRecord", "UpstreamGateEvidence", "UpstreamGatePort",
    "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", "export_m05_evidence", "persist_m05_handoff",
]
