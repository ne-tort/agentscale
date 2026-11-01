"""Resumable M05 import and fail-closed runtime lifecycle contracts.

Persistence adapters must implement atomic record/checkpoint and compare-and-set
operations. This module deliberately grants no authority without dependency gates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol, Sequence


class ImportOutcome(StrEnum):
    IMPORTED = "imported"
    REPLAY = "replay"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class InstanceImportCandidate:
    source_kind: str
    source_id: str
    schema_version: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.source_kind, self.source_id, self.schema_version)):
            raise ValueError("source kind, id and schema version are required")

    @property
    def digest(self) -> str:
        encoded = json.dumps(dict(self.payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ImportCheckpoint:
    partition: str
    after_source_id: str | None = None
    sequence: int = 0

    def __post_init__(self) -> None:
        if not self.partition.strip() or self.sequence < 0:
            raise ValueError("checkpoint partition and non-negative sequence are required")


@dataclass(frozen=True, slots=True)
class InstanceImportRecord:
    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str
    checkpoint: ImportCheckpoint
    outcome: ImportOutcome
    reason_code: str | None
    correlation_id: str


@dataclass(frozen=True, slots=True)
class RuntimeAuditRecord:
    event: str
    outcome: str
    reason_code: str
    correlation_id: str
    plugin_instance_ref: str | None = None
    runtime_generation: int | None = None
    writer_generation: int | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.event, self.outcome, self.reason_code, self.correlation_id)):
            raise ValueError("audit classification and correlation id are required")
        if self.runtime_generation is not None and self.runtime_generation < 0:
            raise ValueError("runtime_generation must be non-negative")
        if self.writer_generation is not None and self.writer_generation < 0:
            raise ValueError("writer_generation must be non-negative")


class InstanceImportStore(Protocol):
    async def find(self, source_kind: str, source_id: str, schema_version: str) -> InstanceImportRecord | None: ...
    async def commit_record_and_checkpoint(self, record: InstanceImportRecord) -> None: ...


class InstanceImportCandidateReader(Protocol):
    async def read_page(self, *, after_source_id: str | None, limit: int) -> Sequence[InstanceImportCandidate]: ...


class RedactedRuntimeAuditPort(Protocol):
    async def append(self, record: RuntimeAuditRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    plugin_instance_id: str
    state: str
    runtime_generation: int
    writer_generation: int

    def __post_init__(self) -> None:
        if not self.plugin_instance_id.strip() or self.runtime_generation < 0 or self.writer_generation < 0:
            raise ValueError("invalid runtime snapshot")
        if self.state not in {"active", "archived", "quarantined"}:
            raise ValueError("unknown lifecycle state")


@dataclass(frozen=True, slots=True)
class WriterCasResult:
    changed: bool
    snapshot: RuntimeSnapshot | None
    reason_code: str


class RuntimeInstanceLifecycle(Protocol):
    async def snapshot(self, plugin_instance_id: str) -> RuntimeSnapshot | None: ...
    async def compare_and_set_writer(self, *, plugin_instance_id: str, expected_runtime_generation: int,
                                     expected_writer_generation: int, next_writer_generation: int) -> WriterCasResult: ...
    async def compare_and_set_runtime(self, *, plugin_instance_id: str, expected_runtime_generation: int,
                                      next_runtime_generation: int) -> WriterCasResult: ...


class ResolvePluginInstancePort(Protocol):
    """Resolve exact active runtime identity; the application facade denies by default."""
    async def resolve(self, *, plugin_instance_id: str, definition_id: str, version_id: str,
                      tenant_id: str, scope_kind: str, scope_id: str, artifact_digest: str,
                      subject_id: str, session_id: str, runtime_generation: int,
                      writer_generation: int) -> object | None: ...


class InstanceImportService:
    """One-page resumable import. Dependencies are opt-in and default blocked."""

    def __init__(self, reader: InstanceImportCandidateReader, store: InstanceImportStore,
                 *, page_size: int = 100, dependencies_ready: bool = False) -> None:
        if page_size < 1 or page_size > 1000:
            raise ValueError("page_size must be between 1 and 1000")
        self._reader, self._store = reader, store
        self._page_size, self._dependencies_ready = page_size, dependencies_ready

    async def run_page(self, checkpoint: ImportCheckpoint, *, correlation_id: str) -> tuple[InstanceImportRecord, ...]:
        if not correlation_id.strip():
            raise ValueError("correlation_id is required")
        if not self._dependencies_ready:
            return (InstanceImportRecord("migration", "MIG-M05-02", "1", "sha256:" + "0" * 64,
                                         checkpoint, ImportOutcome.BLOCKED, "dependency_gate_missing", correlation_id),)
        rows = tuple(await self._reader.read_page(after_source_id=checkpoint.after_source_id, limit=self._page_size))
        if len({row.source_id for row in rows}) != len(rows):
            raise ValueError("candidate page contains duplicate source ids")
        out: list[InstanceImportRecord] = []
        sequence = checkpoint.sequence
        for row in sorted(rows, key=lambda item: item.source_id):
            prior = await self._store.find(row.source_kind, row.source_id, row.schema_version)
            if prior is not None and prior.source_digest == row.digest:
                out.append(InstanceImportRecord(row.source_kind, row.source_id, row.schema_version, row.digest,
                                                prior.checkpoint, ImportOutcome.REPLAY, None, correlation_id))
                sequence += 1
                continue
            outcome = ImportOutcome.CONFLICT if prior is not None else ImportOutcome.QUARANTINED
            reason = "source_digest_changed" if prior is not None else "upstream_identity_and_lifecycle_evidence_missing"
            next_checkpoint = ImportCheckpoint(checkpoint.partition, row.source_id, sequence + 1)
            record = InstanceImportRecord(row.source_kind, row.source_id, row.schema_version, row.digest,
                                          next_checkpoint, outcome, reason, correlation_id)
            await self._store.commit_record_and_checkpoint(record)
            out.append(record)
            sequence += 1
        return tuple(out)


class FailClosedResolvePluginInstance:
    """Resolver facade which never admits a candidate without all upstream gates."""

    def __init__(self, delegate: ResolvePluginInstancePort | None, audit: RedactedRuntimeAuditPort,
                 *, dependencies_ready: bool = False) -> None:
        self._delegate, self._audit, self._dependencies_ready = delegate, audit, dependencies_ready

    async def resolve(self, *, correlation_id: str, **query: object) -> object | None:
        required = {"plugin_instance_id", "definition_id", "version_id", "tenant_id", "scope_kind",
                    "scope_id", "artifact_digest", "subject_id", "session_id", "runtime_generation", "writer_generation"}
        if set(query) != required or not self._dependencies_ready or self._delegate is None:
            await self._audit.append(RuntimeAuditRecord("plugin_instance.resolve", "denied", "dependency_or_query_gate_missing",
                                                       correlation_id))
            return None
        # Audit identity and arguments are intentionally omitted; delegate must validate all supplied bindings.
        return await self._delegate.resolve(**query)  # type: ignore[arg-type]


async def advance_writer_generation(lifecycle: RuntimeInstanceLifecycle, *, plugin_instance_id: str,
                                    expected_runtime_generation: int, expected_writer_generation: int) -> WriterCasResult:
    """CAS permits one-step monotonic writer advancement; stale runtime/workers deny."""
    if expected_runtime_generation < 0 or expected_writer_generation < 0:
        return WriterCasResult(False, None, "invalid_generation")
    result = await lifecycle.compare_and_set_writer(
        plugin_instance_id=plugin_instance_id,
        expected_runtime_generation=expected_runtime_generation,
        expected_writer_generation=expected_writer_generation,
        next_writer_generation=expected_writer_generation + 1,
    )
    if not result.changed or result.snapshot is None:
        return WriterCasResult(False, None, "writer_cas_conflict")
    snap = result.snapshot
    if (snap.plugin_instance_id != plugin_instance_id or snap.state != "active"
            or snap.runtime_generation != expected_runtime_generation
            or snap.writer_generation != expected_writer_generation + 1):
        return WriterCasResult(False, None, "stale_worker_denied")
    return result


async def advance_runtime_generation(lifecycle: RuntimeInstanceLifecycle, *, plugin_instance_id: str,
                                     expected_runtime_generation: int) -> WriterCasResult:
    """Runtime restart/activation generation is strictly monotonic and CAS-protected."""
    if expected_runtime_generation < 0:
        return WriterCasResult(False, None, "invalid_generation")
    result = await lifecycle.compare_and_set_runtime(
        plugin_instance_id=plugin_instance_id,
        expected_runtime_generation=expected_runtime_generation,
        next_runtime_generation=expected_runtime_generation + 1,
    )
    if not result.changed or result.snapshot is None:
        return WriterCasResult(False, None, "runtime_cas_conflict")
    snap = result.snapshot
    if snap.plugin_instance_id != plugin_instance_id or snap.runtime_generation != expected_runtime_generation + 1:
        return WriterCasResult(False, None, "stale_runtime_denied")
    return result
