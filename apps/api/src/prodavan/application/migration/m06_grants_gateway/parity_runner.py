"""Bounded, deterministic M06 parity runner for one promoted partition.

The runner walks shadow evidence in a stable order, enforces an explicit observation
budget, resumes from a checkpoint cursor, and emits a report whose JSON form is
stable across runs. Repeating the same run key with the same evidence is an
idempotent replay; the same key with different evidence is a conflict, never an
overwrite. Parity is evidence for review only: the legacy gateway keeps both the
decision and the read ownership, and ``canonical_positive_writes`` stays false.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence

from .adapter import (
    SCHEMA_VERSION,
    DependencyGates,
    ShadowObservation,
    digest,
    is_sha256,
)

REPORT_SCHEMA = "m06-parity-report-v1"
MAX_LISTED_MISMATCHES = 100


class ParityStatus(StrEnum):
    READY_FOR_REVIEW = "ready_for_review"
    INCOMPLETE = "incomplete_partition"
    BLOCKED = "blocked"


class ReportOutcome(StrEnum):
    RECORDED = "recorded"
    REPLAY = "replay"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ParityPartition:
    """Bounded partition key, optionally narrowed by version or exact instance."""

    tenant_id: str
    app_definition_id: str
    app_version_id: str | None = None
    plugin_instance_id: str | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.app_definition_id.strip():
            raise ValueError("parity partition requires tenant and app definition")
        for value in (self.app_version_id, self.plugin_instance_id):
            if value is not None and not value.strip():
                raise ValueError("narrowed partition anchors cannot be blank")

    @property
    def digest(self) -> str:
        return digest({"tenant_id": self.tenant_id, "app_definition_id": self.app_definition_id,
                       "app_version_id": self.app_version_id,
                       "plugin_instance_id": self.plugin_instance_id})


@dataclass(frozen=True, slots=True)
class ParityEvidenceSummary:
    """Trusted, partition-bound evidence counts for the observed window."""

    partition: ParityPartition
    source_snapshot_digest: str
    final_delta_checkpoint: str
    writer_epoch: int
    unknown_mapping_count: int
    unresolved_positive_bindings: int
    stale_generation_acceptances: int
    audit_complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.partition, ParityPartition):
            raise ValueError("evidence summary must bind a parity partition")
        if not is_sha256(self.source_snapshot_digest):
            raise ValueError("source snapshot digest must be sha256")
        if not self.final_delta_checkpoint.strip():
            raise ValueError("final delta checkpoint is required")
        if not isinstance(self.writer_epoch, int) or isinstance(self.writer_epoch, bool) or self.writer_epoch <= 0:
            raise ValueError("writer epoch must be positive")
        if min(self.unknown_mapping_count, self.unresolved_positive_bindings,
               self.stale_generation_acceptances) < 0:
            raise ValueError("evidence counts must be non-negative")
        if not isinstance(self.audit_complete, bool):
            raise ValueError("audit completeness must be explicit")


class ParityEvidenceSummaryPort(Protocol):
    async def load_summary(self, *, context: Any, partition: ParityPartition,
                           window_start: datetime, window_end: datetime
                           ) -> ParityEvidenceSummary | None: ...


class ObservationReadPort(Protocol):
    async def load_observations(self, *, context: Any, tenant_id: str, app_definition_id: str,
                                window_start: datetime, window_end: datetime,
                                after_cursor: str | None = None, limit: int = 500
                                ) -> "ObservationBatch": ...


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    observations: tuple[ShadowObservation, ...]
    next_cursor: str | None
    truncated: bool


class ParityReportStorePort(Protocol):
    """Append-only report registry keyed by the deterministic run key.

    The server context is passed through so a durable registry can bind the write
    to the partition's tenant and RLS scope. A durable implementation must refuse a
    missing context; ``InMemoryParityReportStore`` is a test double that ignores it
    and must never be composed in a production path.
    """

    async def load(self, *, run_key: str, context: Any | None = None
                   ) -> Mapping[str, Any] | None: ...

    async def save(self, *, run_key: str, report: Mapping[str, Any],
                   context: Any | None = None) -> ReportOutcome: ...


EMPTY_WINDOW_CURSOR = "empty-window"
MATERIALIZED_WINDOW_CURSOR = "materialized-window"
"""Cursors recorded for a complete traversal that has no page cursor to report.

A complete window must still name the position the read stopped at, because ``None``
there reads as "no traversal happened" and could be resumed or re-recorded as if the
window were untouched. An empty page keeps a stable token, and a caller that aggregates
already-materialized evidence (no paging at all) records the materialized token.
"""


@dataclass(frozen=True, slots=True)
class ParityCheckpoint:
    """Resumable traversal position; never a parity conclusion on its own."""

    cursor: str | None
    observation_count: int
    complete_window: bool

    def __post_init__(self) -> None:
        if self.cursor is not None and not self.cursor.strip():
            raise ValueError("checkpoint cursor cannot be blank")
        if self.observation_count < 0:
            raise ValueError("checkpoint observation count must be non-negative")
        if self.complete_window and self.cursor is None:
            raise ValueError("a complete window needs the final cursor it stopped at")

    @classmethod
    def for_complete_window(cls, cursor: str | None,
                            observation_count: int) -> "ParityCheckpoint":
        """Checkpoint for a traversal that consumed the whole requested window."""
        if observation_count < 0:
            raise ValueError("checkpoint observation count must be non-negative")
        if cursor is None:
            if observation_count:
                raise ValueError("a complete window needs the final cursor it stopped at")
            cursor = EMPTY_WINDOW_CURSOR
        return cls(cursor, observation_count, True)

    @classmethod
    def for_materialized_window(cls, observation_count: int) -> "ParityCheckpoint":
        """Checkpoint for evidence that was already collected instead of paged."""
        if observation_count < 0:
            raise ValueError("checkpoint observation count must be non-negative")
        return cls(MATERIALIZED_WINDOW_CURSOR, observation_count, True)

    @property
    def digest(self) -> str:
        return digest({"cursor": self.cursor, "observation_count": self.observation_count,
                       "complete_window": self.complete_window})


@dataclass(frozen=True, slots=True)
class ParityRunRequest:
    partition: ParityPartition
    window_start: datetime
    window_end: datetime
    max_observations: int = 1000
    page_size: int = 200
    start_cursor: str | None = None

    def __post_init__(self) -> None:
        for value in (self.window_start, self.window_end):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("parity window must be timezone-aware")
        if self.window_start >= self.window_end:
            raise ValueError("parity window must be increasing")
        if not 1 <= self.max_observations <= 10_000:
            raise ValueError("max_observations must be between 1 and 10000")
        if not 1 <= self.page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if self.start_cursor is not None and not self.start_cursor.strip():
            raise ValueError("start cursor cannot be blank")

    @property
    def digest(self) -> str:
        return digest({
            "partition": self.partition.digest,
            "window_start": self.window_start.astimezone(UTC).isoformat(),
            "window_end": self.window_end.astimezone(UTC).isoformat(),
            "max_observations": self.max_observations,
            "page_size": self.page_size,
            "start_cursor": self.start_cursor,
        })


@dataclass(frozen=True, slots=True)
class UnexplainedMismatch:
    """One legacy/canonical disagreement that blocks promotion of the partition."""

    source_id_digest: str
    capability_key: str
    legacy_outcome: str
    canonical_outcome: str
    observed_at: str
    correlation_id: str

    @property
    def digest(self) -> str:
        return digest({"source_id_digest": self.source_id_digest, "capability_key": self.capability_key,
                       "legacy": self.legacy_outcome, "canonical": self.canonical_outcome,
                       "observed_at": self.observed_at, "correlation_id": self.correlation_id})


@dataclass(frozen=True, slots=True)
class ParityRunReport:
    schema: str
    run_key: str
    request_digest: str
    partition_digest: str
    tenant_id: str
    app_definition_id: str
    observation_window_start: str
    observation_window_end: str
    source_snapshot_digest: str
    final_delta_checkpoint: str
    writer_epoch: int
    observation_count: int
    mismatch_count: int
    explained_mismatch_count: int
    unexplained_mismatch_count: int
    unknown_mapping_count: int
    unresolved_positive_bindings: int
    stale_generation_acceptances: int
    audit_complete: bool
    source_digest: str
    canonical_digest: str
    binding_digest: str
    operation_mapping_digest: str
    checkpoint: ParityCheckpoint
    status: str
    blocked_reasons: tuple[str, ...]
    unexplained: tuple[UnexplainedMismatch, ...]
    decision_owner: str = "legacy"
    canonical_read_owner: str = "legacy"
    canonical_positive_writes: bool = False
    report_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema != REPORT_SCHEMA:
            raise ValueError("unsupported parity report schema")
        if self.decision_owner != "legacy" or self.canonical_read_owner != "legacy":
            raise ValueError("parity evidence cannot transfer decision ownership")
        if self.canonical_positive_writes is not False:
            raise ValueError("parity evidence cannot enable canonical positive writes")
        if self.status not in {item.value for item in ParityStatus}:
            raise ValueError("unsupported parity status")
        if not isinstance(self.blocked_reasons, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.blocked_reasons
        ):
            raise ValueError("blocked reasons must be stable non-empty strings")
        if self.blocked_reasons != tuple(sorted(self.blocked_reasons)):
            raise ValueError("blocked reasons must be canonically ordered")
        if self.status == ParityStatus.READY_FOR_REVIEW.value and self.blocked_reasons:
            raise ValueError("a ready report cannot carry blocked reasons")
        if self.status != ParityStatus.BLOCKED.value and not is_sha256(self.source_snapshot_digest):
            raise ValueError("an evaluated report requires the source snapshot digest")
        if self.status != ParityStatus.BLOCKED.value and (
            not self.final_delta_checkpoint.strip() or self.writer_epoch <= 0
        ):
            raise ValueError("an evaluated report requires the final delta checkpoint and writer epoch")
        if self.unexplained_mismatch_count and not self.unexplained:
            raise ValueError("unexplained mismatches must be listed for review")
        if len(self.unexplained) > self.unexplained_mismatch_count:
            raise ValueError("listed mismatches cannot exceed the unexplained count")
        expected = self.computed_digest()
        if self.report_digest and self.report_digest != expected:
            raise ValueError("parity report digest does not match its content")
        object.__setattr__(self, "report_digest", expected)

    def content(self) -> Mapping[str, Any]:
        """Canonical report content; the digest and JSON export use exactly this."""
        return {
            "schema": self.schema,
            "run_key": self.run_key,
            "request_digest": self.request_digest,
            "partition_digest": self.partition_digest,
            "tenant_id": self.tenant_id,
            "app_definition_id": self.app_definition_id,
            "observation_window_start": self.observation_window_start,
            "observation_window_end": self.observation_window_end,
            "source_snapshot_digest": self.source_snapshot_digest,
            "final_delta_checkpoint": self.final_delta_checkpoint,
            "writer_epoch": self.writer_epoch,
            "observation_count": self.observation_count,
            "mismatch_count": self.mismatch_count,
            "explained_mismatch_count": self.explained_mismatch_count,
            "unexplained_mismatch_count": self.unexplained_mismatch_count,
            "unknown_mapping_count": self.unknown_mapping_count,
            "unresolved_positive_bindings": self.unresolved_positive_bindings,
            "stale_generation_acceptances": self.stale_generation_acceptances,
            "audit_complete": self.audit_complete,
            "digests": {
                "source": self.source_digest,
                "canonical": self.canonical_digest,
                "binding": self.binding_digest,
                "operation_mapping": self.operation_mapping_digest,
            },
            "checkpoint": {
                "cursor": self.checkpoint.cursor,
                "observation_count": self.checkpoint.observation_count,
                "complete_window": self.checkpoint.complete_window,
                "digest": self.checkpoint.digest,
            },
            "status": self.status,
            "blocked_reasons": list(self.blocked_reasons),
            "unexplained": [
                {"source_id_digest": item.source_id_digest, "capability_key": item.capability_key,
                 "legacy": item.legacy_outcome, "canonical": item.canonical_outcome,
                 "observed_at": item.observed_at, "correlation_id": item.correlation_id,
                 "digest": item.digest}
                for item in self.unexplained
            ],
            "ownership": {
                "decision_owner": self.decision_owner,
                "canonical_read_owner": self.canonical_read_owner,
                "canonical_positive_writes": self.canonical_positive_writes,
            },
        }

    def computed_digest(self) -> str:
        return digest(self.content())

    def to_json(self) -> dict[str, Any]:
        """Stable JSON-ready structure: fixed key set, sorted lists, no timestamps drift."""
        body = dict(self.content())
        body["report_digest"] = self.report_digest
        body["report_schema_version"] = SCHEMA_VERSION
        return body

    def export(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_json(), sort_keys=True, indent=indent, ensure_ascii=True)


class BoundedParityRunner:
    """Traverse one bounded partition deterministically and report parity evidence."""

    def __init__(self, *, observations: ObservationReadPort, summaries: ParityEvidenceSummaryPort,
                 reports: ParityReportStorePort | None = None) -> None:
        self._observations = observations
        self._summaries = summaries
        self._reports = reports

    async def run(self, *, request: ParityRunRequest, context: Any,
                  dependency_gates: DependencyGates | None = None) -> ParityRunReport:
        if not isinstance(request, ParityRunRequest):
            raise ValueError("parity run requires an explicit bounded request")
        _require_partition_context(context, request.partition)
        if (request.partition.app_version_id is not None
                or request.partition.plugin_instance_id is not None):
            # Shadow observations carry no version/instance anchor, so a narrowed
            # partition cannot be verified after the read. Refuse rather than report
            # parity for a wider window than the one actually requested.
            return _blocked_report(request, ("partition_narrowing_unsupported",),
                                   dependency_gates)
        summary = await self._summaries.load_summary(
            context=context, partition=request.partition, window_start=request.window_start,
            window_end=request.window_end)
        if summary is None:
            return _blocked_report(request, ("partition_evidence_summary_missing",), dependency_gates)
        if summary.partition != request.partition:
            return _blocked_report(request, ("partition_evidence_summary_mismatch",), dependency_gates)

        collected: list[ShadowObservation] = []
        seen: set[str] = set()
        cursor = request.start_cursor
        previous_cursor: str | None = cursor
        complete = False
        pages = 0
        # Every page that reports ``truncated`` must be followed by a further page; a
        # source that never completes the window would otherwise be an unbounded read.
        max_pages = request.max_observations + 2
        while len(collected) < request.max_observations:
            if pages >= max_pages:
                # A source that keeps reporting a truncated page without consuming the
                # budget is a broken traversal, not a long partition.
                return _blocked_report(request, ("observation_page_budget_exhausted",),
                                       dependency_gates)
            pages += 1
            limit = min(request.page_size, request.max_observations - len(collected))
            batch = await self._observations.load_observations(
                context=context, tenant_id=request.partition.tenant_id,
                app_definition_id=request.partition.app_definition_id,
                window_start=request.window_start, window_end=request.window_end,
                after_cursor=cursor, limit=limit,
            )
            if not isinstance(batch, ObservationBatch):
                return _blocked_report(request, ("observation_page_invalid",), dependency_gates)
            if len(batch.observations) > limit:
                return _blocked_report(request, ("observation_page_exceeds_page_size",),
                                       dependency_gates)
            if previous_cursor is not None and batch.next_cursor is not None:
                if not _monotonic(previous_cursor, batch.next_cursor):
                    return _blocked_report(request, ("observation_cursor_not_monotonic",),
                                           dependency_gates)
            for observation in batch.observations:
                identity = digest([observation.source_id, observation.capability_key,
                                   observation.correlation_id, observation.observed_at])
                if identity in seen:
                    return _blocked_report(request, ("duplicate_observation_evidence",),
                                           dependency_gates)
                seen.add(identity)
                collected.append(observation)
            if len(collected) > request.max_observations:
                return _blocked_report(request, ("observation_budget_exceeded",),
                                       dependency_gates)
            cursor = batch.next_cursor
            previous_cursor = cursor
            if not batch.truncated:
                complete = True
                break
            if batch.next_cursor is None:
                return _blocked_report(request, ("observation_cursor_missing",), dependency_gates)
        checkpoint = (ParityCheckpoint.for_complete_window(cursor, len(collected)) if complete
                      else ParityCheckpoint(cursor, len(collected), False))
        return _build_report(
            request=request, summary=summary, observations=tuple(collected),
            checkpoint=checkpoint, dependency_gates=dependency_gates,
        )

    async def run_and_record(self, *, request: ParityRunRequest, context: Any,
                             dependency_gates: DependencyGates | None = None
                             ) -> tuple[ParityRunReport, ReportOutcome | None]:
        """Run and persist; a partial traversal is returned without being recorded.

        The server context is forwarded to the registry: a durable store binds the
        write to the partition's tenant and RLS scope, so a report can never be
        recorded under another tenant.
        """
        report = await self.run(request=request, context=context, dependency_gates=dependency_gates)
        if not report.checkpoint.complete_window or self._reports is None:
            return report, None
        return report, await self._reports.save(
            run_key=report.run_key, report=report.to_json(), context=context)


def run_key_for(summary: ParityEvidenceSummary, request: ParityRunRequest) -> str:
    """Deterministic registry key for one requested parity run.

    The key binds what was *asked*: the partition, the window, the observation budget
    and the source position the run was meant to evaluate (final delta checkpoint and
    writer epoch). The digest of the evidence actually observed
    (``source_snapshot_digest``) is deliberately excluded: re-running the same request
    and finding different evidence must surface as an explicit registry conflict, not
    as a silent second report recorded under a freshly derived key.
    """
    return digest({
        "report_schema": REPORT_SCHEMA,
        "partition": request.partition.digest,
        "window_start": request.window_start.astimezone(UTC).isoformat(),
        "window_end": request.window_end.astimezone(UTC).isoformat(),
        "final_delta_checkpoint": summary.final_delta_checkpoint,
        "writer_epoch": summary.writer_epoch,
        "max_observations": request.max_observations,
    })


def _build_report(*, request: ParityRunRequest, summary: ParityEvidenceSummary,
                  observations: Sequence[ShadowObservation], checkpoint: ParityCheckpoint,
                  dependency_gates: DependencyGates | None) -> ParityRunReport:
    ordered = tuple(sorted(observations, key=lambda row: (
        datetime.fromisoformat(row.observed_at), row.source_id, row.capability_key,
        row.correlation_id, row.source_digest, row.canonical_digest or "",
        row.binding_digest or "", row.operation_mapping_digest,
    )))
    for row in ordered:
        if row.tenant_id != request.partition.tenant_id:
            raise ValueError("observation lies outside the requested partition")
        if row.app_definition_id != request.partition.app_definition_id:
            raise ValueError("observation lies outside the requested partition")
        if not request.window_start <= datetime.fromisoformat(row.observed_at) < request.window_end:
            raise ValueError("observation lies outside the requested parity window")
    mismatches = tuple(row for row in ordered if row.mismatch)
    explained = tuple(row for row in mismatches if row.mismatch_explained)
    unexplained = tuple(row for row in mismatches if not row.mismatch_explained)
    blocked: list[str] = []
    if dependency_gates is None:
        blocked.append("accepted_upstream_and_atomic_evidence_handoffs")
    else:
        blocked.extend(dependency_gates.missing_markers)
    # The coverage/empty-window facts are reported in ``blocked_reasons`` even when the
    # partition is still being traversed: a reviewer must see why the run did not
    # conclude, not only that the status was incomplete.
    if not ordered:
        blocked.append("no_shadow_observations")
    if not checkpoint.complete_window:
        blocked.append("partition_coverage_incomplete")
    if unexplained:
        blocked.append("unexplained_decision_mismatch")
    if summary.unknown_mapping_count:
        blocked.append("unknown_mappings_present")
    if summary.unresolved_positive_bindings:
        blocked.append("unresolved_positive_bindings_present")
    if summary.stale_generation_acceptances:
        blocked.append("stale_generation_acceptances_present")
    if summary.audit_complete is not True:
        blocked.append("audit_evidence_incomplete")
    if any(row.canonical_digest is None or row.binding_digest is None for row in ordered):
        blocked.append("unresolved_canonical_or_binding_digest")
    if checkpoint.complete_window and not ordered:
        blocked.append("empty_observation_window")
    # Traversal state decides first: a window that was never fully consumed is an
    # incomplete observation of the partition and must say so, even though every
    # blocker it already discovered is listed in ``blocked_reasons``. Only a complete
    # window can conclude, and then any outstanding blocker outranks readiness.
    if not checkpoint.complete_window:
        status = ParityStatus.INCOMPLETE
    elif blocked:
        status = ParityStatus.BLOCKED
    else:
        status = ParityStatus.READY_FOR_REVIEW
    listed = tuple(UnexplainedMismatch(
        source_id_digest=digest(row.source_id),
        capability_key=row.capability_key,
        legacy_outcome=row.legacy_decision.reason_code,
        canonical_outcome=row.canonical_decision.reason_code,
        observed_at=row.observed_at,
        correlation_id=row.correlation_id,
    ) for row in unexplained[:MAX_LISTED_MISMATCHES])
    return ParityRunReport(
        schema=REPORT_SCHEMA,
        run_key=run_key_for(summary, request),
        request_digest=request.digest,
        partition_digest=request.partition.digest,
        tenant_id=request.partition.tenant_id,
        app_definition_id=request.partition.app_definition_id,
        observation_window_start=request.window_start.astimezone(UTC).isoformat(),
        observation_window_end=request.window_end.astimezone(UTC).isoformat(),
        source_snapshot_digest=summary.source_snapshot_digest,
        final_delta_checkpoint=summary.final_delta_checkpoint,
        writer_epoch=summary.writer_epoch,
        observation_count=len(ordered),
        mismatch_count=len(mismatches),
        explained_mismatch_count=len(explained),
        unexplained_mismatch_count=len(unexplained),
        unknown_mapping_count=summary.unknown_mapping_count,
        unresolved_positive_bindings=summary.unresolved_positive_bindings,
        stale_generation_acceptances=summary.stale_generation_acceptances,
        audit_complete=summary.audit_complete,
        source_digest=digest([row.source_digest for row in ordered]),
        canonical_digest=digest([row.canonical_digest or "quarantine" for row in ordered]),
        binding_digest=digest([row.binding_digest or "unresolved" for row in ordered]),
        operation_mapping_digest=digest([row.operation_mapping_digest for row in ordered]),
        checkpoint=checkpoint,
        status=status.value,
        blocked_reasons=tuple(sorted(set(blocked))),
        unexplained=listed,
    )


def _blocked_report(request: ParityRunRequest, reasons: Sequence[str],
                    dependency_gates: DependencyGates | None) -> ParityRunReport:
    blocked = set(reasons)
    blocked.update(dependency_gates.missing_markers if dependency_gates is not None
                   else ("accepted_upstream_and_atomic_evidence_handoffs",))
    return ParityRunReport(
        schema=REPORT_SCHEMA,
        run_key=digest({"request": request.digest, "blocked": sorted(blocked)}),
        request_digest=request.digest,
        partition_digest=request.partition.digest,
        tenant_id=request.partition.tenant_id,
        app_definition_id=request.partition.app_definition_id,
        observation_window_start=request.window_start.astimezone(UTC).isoformat(),
        observation_window_end=request.window_end.astimezone(UTC).isoformat(),
        source_snapshot_digest="sha256:" + "0" * 64,
        final_delta_checkpoint="",
        writer_epoch=0,
        observation_count=0,
        mismatch_count=0,
        explained_mismatch_count=0,
        unexplained_mismatch_count=0,
        unknown_mapping_count=0,
        unresolved_positive_bindings=0,
        stale_generation_acceptances=0,
        audit_complete=False,
        source_digest=digest([]),
        canonical_digest=digest([]),
        binding_digest=digest([]),
        operation_mapping_digest=digest([]),
        checkpoint=ParityCheckpoint(None, 0, False),
        status=ParityStatus.BLOCKED.value,
        blocked_reasons=tuple(sorted(blocked)),
        unexplained=(),
    )


def _require_partition_context(context: Any, partition: ParityPartition) -> None:
    if not getattr(context, "authenticated", False):
        raise ValueError("parity run requires an authenticated server context")
    if getattr(context, "tenant_id", None) != partition.tenant_id:
        raise ValueError("parity run context tenant does not match the requested partition")
    if getattr(context, "app_definition_id", None) != partition.app_definition_id:
        raise ValueError("parity run context definition does not match the requested partition")
    if not getattr(context, "correlation_id", "").strip():
        raise ValueError("parity run requires a correlation id")


def _monotonic(previous: str, current: str) -> bool:
    try:
        return int(current) > int(previous)
    except (TypeError, ValueError):
        return current > previous


__all__ = [
    "BoundedParityRunner",
    "MAX_LISTED_MISMATCHES",
    "ObservationBatch",
    "ObservationReadPort",
    "ParityCheckpoint",
    "ParityEvidenceSummary",
    "ParityEvidenceSummaryPort",
    "ParityPartition",
    "ParityReportStorePort",
    "ParityRunReport",
    "ParityRunRequest",
    "ParityStatus",
    "REPORT_SCHEMA",
    "ReportOutcome",
    "UnexplainedMismatch",
    "run_key_for",
]
