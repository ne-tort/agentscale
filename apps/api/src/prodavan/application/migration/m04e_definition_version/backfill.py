"""Bounded evidence-only definition/version backfill contracts and coordinator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Sequence


class EvidenceStatus(StrEnum):
    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"


class BackfillOutcome(StrEnum):
    ACCEPTED = "accepted"
    REPLAY = "replay"
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


class HandoffStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    source_kind: str
    source_id: str
    schema_version: str
    payload: dict[str, object]
    phase: str
    tenant_id: str | None = None
    app_definition_id: str | None = None

    def __post_init__(self) -> None:
        if self.phase not in {"definition", "version"}:
            raise ValueError("phase must be definition or version")
        if not self.source_kind.strip() or not self.source_id.strip() or not self.schema_version.strip():
            raise ValueError("source identity and schema version are required")
        if self.phase == "version" and not self.app_definition_id:
            raise ValueError("version evidence requires its definition identity")

    @property
    def source_digest(self) -> str:
        encoded = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @property
    def canonical_identity(self) -> tuple[str, ...]:
        if self.phase == "definition":
            return (self.tenant_id or "", str(self.payload.get("app_id", "")))
        return (self.app_definition_id or "", str(self.payload.get("manifest_version", "")))


@dataclass(frozen=True, slots=True)
class BackfillCheckpoint:
    partition: tuple[str, str]
    phase: str
    after_source_id: str | None
    sequence: int


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


@dataclass(frozen=True, slots=True)
class BackfillBatch:
    records: tuple[BackfillRecord, ...]
    checkpoint: BackfillCheckpoint
    complete: bool


class EvidenceStore(Protocol):
    async def find(self, source_kind: str, source_id: str, schema_version: str) -> BackfillRecord | None: ...
    async def append(self, record: BackfillRecord) -> None: ...


class CandidateReader(Protocol):
    async def read_page(
        self, *, phase: str, after_source_id: str | None, limit: int
    ) -> Sequence[EvidenceCandidate]: ...


@dataclass(frozen=True, slots=True)
class HandoffGate:
    name: str
    passed: bool
    evidence_digest: str | None = None
    reason: str | None = None


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


class DefinitionVersionEvidenceBackfill:
    """Process source rows in dependency order with bounded, digest-safe replay."""

    def __init__(
        self,
        reader: CandidateReader,
        store: EvidenceStore,
        *,
        page_size: int = 100,
        dependencies_ready: bool = False,
    ) -> None:
        if page_size < 1 or page_size > 1000:
            raise ValueError("page_size must be between 1 and 1000")
        self._reader = reader
        self._store = store
        self._page_size = page_size
        self._dependencies_ready = dependencies_ready

    async def run_page(self, checkpoint: BackfillCheckpoint, *, correlation_id: str) -> BackfillBatch:
        if not self._dependencies_ready:
            blocked = BackfillRecord(
                source_kind="migration", source_id="MIG-M04E-02", schema_version="1",
                source_digest="sha256:" + "0" * 64, canonical_digest=None, canonical_identity=(),
                checkpoint=checkpoint, status=EvidenceStatus.QUARANTINED,
                reason_code="dependency_gate_missing", correlation_id=correlation_id,
            )
            return BackfillBatch((blocked,), checkpoint, False)
        rows = tuple(await self._reader.read_page(
            phase=checkpoint.phase, after_source_id=checkpoint.after_source_id, limit=self._page_size
        ))
        if any(row.phase != checkpoint.phase for row in rows):
            raise ValueError("reader returned a row outside the current dependency phase")
        ordered = tuple(sorted(rows, key=lambda item: item.source_id))
        records: list[BackfillRecord] = []
        sequence = checkpoint.sequence
        after = checkpoint.after_source_id
        for row in ordered:
            prior = await self._store.find(row.source_kind, row.source_id, row.schema_version)
            status = EvidenceStatus.ACCEPTED
            reason = None
            canonical_digest = self._digest_identity(row.canonical_identity)
            conflict_id = row.source_id
            if prior is not None:
                if prior.source_digest == row.source_digest:
                    records.append(prior)
                    after = row.source_id
                    sequence += 1
                    continue
                status, reason = EvidenceStatus.CONFLICT, "source_digest_changed"
                # Preserve the original mapping and append the conflict under its own evidence identity.
                conflict_id = row.source_id + "#conflict:" + row.source_digest.removeprefix("sha256:")[:16]
            elif not self._candidate_is_provable(row):
                status, reason, canonical_digest = EvidenceStatus.QUARANTINED, "insufficient_owner_or_artifact_proof", None
            next_checkpoint = BackfillCheckpoint(checkpoint.partition, checkpoint.phase, after, sequence + 1)
            record = BackfillRecord(
                row.source_kind, conflict_id, row.schema_version, row.source_digest,
                canonical_digest, row.canonical_identity, next_checkpoint, status, reason, correlation_id,
            )
            await self._store.append(record)
            records.append(record)
            after = row.source_id
            sequence += 1
        complete = len(rows) < self._page_size
        next_phase = "version" if complete and checkpoint.phase == "definition" else checkpoint.phase
        next_checkpoint = BackfillCheckpoint(
            checkpoint.partition, next_phase, None if next_phase != checkpoint.phase else after, sequence
        )
        return BackfillBatch(tuple(records), next_checkpoint, complete and next_phase == "version")

    @staticmethod
    def _candidate_is_provable(row: EvidenceCandidate) -> bool:
        if not row.tenant_id or not row.app_definition_id or not row.canonical_identity or any(not part.strip() for part in row.canonical_identity):
            return False
        if row.phase == "version":
            artifact = row.payload.get("artifact_digest")
            lifecycle = row.payload.get("status")
            return isinstance(artifact, str) and artifact.startswith("sha256:") and lifecycle in {
                "draft", "published", "disabled", "revoked"
            }
        return bool(row.payload.get("app_id"))

    @staticmethod
    def _digest_identity(identity: tuple[str, ...]) -> str:
        raw = json.dumps(identity, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def export_m05_evidence(
    *, tenant_id: str, app_definition_id: str, records: Sequence[BackfillRecord],
    source_snapshot_digest: str, checkpoint: BackfillCheckpoint, gates: Sequence[HandoffGate],
) -> M05EvidenceExport:
    partition_records = tuple(
        row for row in records
        if row.checkpoint.partition == (tenant_id, app_definition_id)
    )
    accepted = tuple(row for row in partition_records if row.status is EvidenceStatus.ACCEPTED)
    quarantined = tuple(row for row in partition_records if row.status is not EvidenceStatus.ACCEPTED)
    all_gates = tuple(gates)
    ready = bool(partition_records) and not quarantined and bool(all_gates) and all(gate.passed and gate.evidence_digest for gate in all_gates)
    canonical_digest = DefinitionVersionEvidenceBackfill._digest_identity(tuple(sorted(
        f"{row.source_id}:{row.canonical_digest}" for row in accepted
    )))
    return M05EvidenceExport(
        tenant_id=tenant_id, app_definition_id=app_definition_id,
        source_snapshot_digest=source_snapshot_digest, canonical_digest=canonical_digest,
        checkpoint=checkpoint, accepted=accepted, quarantined=quarantined, gates=all_gates,
        status=HandoffStatus.READY if ready else HandoffStatus.BLOCKED,
        canonical_positive_writes=False,
    )
