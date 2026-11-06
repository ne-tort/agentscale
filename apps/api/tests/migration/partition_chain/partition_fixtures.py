"""Doubles for the per-partition chain assembly.

Two rules shape everything here.

**1. The session factory refuses to be called.**  The whole point of assembly -- and of a
dry run -- is that it *starts nothing*: no backfill page, no import, no cutover transition,
no transaction.  :class:`RecordingSessionFactory` counts its calls and raises on the first
one, so a test that expected "nothing happened" fails loudly if any composed port or the
default evidence source opened a session.

**2. Only real construction where real construction is possible.**  The partition planner
and the composition root under test are the production ones; the doubles below replace only
what a *partition* genuinely cannot own (an artifact store, a caller-owned SERIALIZABLE
repository, the instance owner's runtime session, the exact M05 upstream evidence, the
legacy capability owners).  Every fabricated object is inert: calling any method on it fails
the test with ``AssertionError``, so "supplied" can never quietly mean "executed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prodavan.application.migration.composition import (
    MigrationChain,
    MigrationStage,
    StageAssembly,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    HandoffStatus as M05HandoffStatus,
    ImportCheckpoint,
    PromotionGateEvidence,
    PromotionRecord,
    TrustedRuntimeContext,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    HandoffStatus as M04EHandoffStatus,
)
from prodavan.application.migration.m06_grants_gateway.composition import (
    ShadowGatewayPorts,
)
from prodavan.application.migration.partition_chain import (
    PartitionAssemblyOverrides,
    PartitionChainKey,
)
from prodavan.application.migration.partition_runner import ScopedEvidenceReadiness

TENANT = "tenant-alpha"
DEFINITION = "definition-alpha"
OTHER_TENANT = "tenant-beta"
OTHER_DEFINITION = "definition-beta"
GENERATION = 3

SNAPSHOT_DIGEST = "sha256:" + "a" * 64
CANONICAL_DIGEST = "sha256:" + "b" * 64
ROWS_DIGEST = "sha256:" + "c" * 64
NOW_TEXT = "2026-11-04T00:00:00+00:00"


def key() -> PartitionChainKey:
    return PartitionChainKey(tenant_id=TENANT, app_definition_id=DEFINITION)


def other_key() -> PartitionChainKey:
    return PartitionChainKey(tenant_id=OTHER_TENANT, app_definition_id=OTHER_DEFINITION)


class RecordingSessionFactory:
    """Callable ``async_sessionmaker`` stand-in: records and refuses every use."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError(
            "assembly and a dry run must not open a database session: the partition planner "
            "only composes, and the runner only reads what it was given"
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"RecordingSessionFactory(calls={self.calls})"


class Inert:
    """A supplied collaborator on which any method call is a test failure."""

    def __getattr__(self, attribute: str) -> Any:
        def refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError(
                f"{attribute} was executed: a partition that only *supplies* a collaborator "
                "must not run it"
            )

        return refuse


class PrincipalResolver:
    """A resolver that is never expected to answer: assembly only requires the callable."""

    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, *, correlation_id: str) -> None:
        self.calls += 1
        raise AssertionError(
            "no principal may be resolved while a partition is merely assembled"
        )


def runtime_context(**overrides: Any) -> TrustedRuntimeContext:
    values: dict[str, Any] = {
        "resolved_tenant_id": TENANT,
        "subject_id": "subject-owner",
        "session_id": "session-owner",
        "authenticated": True,
        "correlation_id": "corr-partition",
    }
    values.update(overrides)
    return TrustedRuntimeContext(**values)


def shadow_ports() -> ShadowGatewayPorts:
    """The full ``ShadowGatewayPorts`` a partition needs from its host.

    ``legacy_scope``/``legacy_quota`` are stated because ``build_shadow_gateway`` refuses
    without both legacy owners: exact parity compares them, so a partition cannot derive
    them and must be given them.
    """

    inert = Inert()
    return ShadowGatewayPorts(
        company=inert,
        m04_scope=inert,
        m04e_definition_versions=inert,
        m05_instances=inert,
        m05_upstream=inert,
        handoff_partitions=inert,
        legacy_repository=inert,
        legacy_scope=inert,
        legacy_quota=inert,
        canonical_grants=inert,
        method_mappings=inert,
        method_servers={"mail": "cfg-mail"},
        method_versions={"mail": "v1"},
        evidence_read=inert,
        evidence_transaction=inert,
    )


def complete_overrides(**overrides: Any) -> PartitionAssemblyOverrides:
    """Every collaborator a partition cannot derive, supplied with inert doubles."""

    values: dict[str, Any] = {
        "principal_resolver": PrincipalResolver(),
        "m04e_artifacts": Inert(),
        "m04e_server_repository": Inert(),
        "m05_runtime_context": runtime_context(),
        "m05_upstream_evidence": Inert(),
        "m05_identity_sessions": Inert(),
        "m06_shadow_ports": shadow_ports(),
    }
    values.update(overrides)
    return PartitionAssemblyOverrides(**values)


def promotion_record(
    *,
    tenant: str = TENANT,
    definition: str = DEFINITION,
    generation: int | None = GENERATION,
    status: M05HandoffStatus = M05HandoffStatus.READY,
    quarantined: int = 0,
    conflicts: int = 0,
) -> PromotionRecord:
    """A minimal M05 promotion record carrying only what the M06 precondition reads."""

    partition = (tenant, definition)
    return PromotionRecord(
        record_id="m05eb:" + definition,
        task_id="MIG-M05-02",
        status=status,
        partition=partition,
        source_snapshot_digest=SNAPSHOT_DIGEST,
        canonical_digest=ROWS_DIGEST,
        checkpoint=ImportCheckpoint(
            partition=partition,
            source_snapshot_id="snapshot-1",
            source_schema_version="legacy-plugin-instance.v1",
            source_snapshot_digest=SNAPSHOT_DIGEST,
        ),
        imported_count=2,
        replay_count=0,
        quarantined_count=quarantined,
        conflict_count=conflicts,
        gate_evidence=(PromotionGateEvidence("M04_scope", True, ROWS_DIGEST, None),),
        actor="migration-operator",
        recorded_at=NOW_TEXT,
        decision_owner="coordinator",
        writer_generation=generation,
        correlation_id="corr-partition",
        m06_handoff=status,
    )


@dataclass(frozen=True, slots=True)
class MarkerStub:
    """The fields the M05 precondition reads off a durable handoff marker."""

    status: Any
    writer_generation: int | None = GENERATION
    quarantined_count: int = 0
    canonical_digest: str = CANONICAL_DIGEST


def ready_marker(**overrides: Any) -> MarkerStub:
    """A durable marker that records acceptance for this partition."""

    values: dict[str, Any] = {
        "status": M04EHandoffStatus.READY,
        "writer_generation": GENERATION,
    }
    values.update(overrides)
    return MarkerStub(**values)


@dataclass
class RecordingEvidenceSource:
    """A partition evidence source that answers from fields and records every read.

    It is deliberately *not* a database double: it proves the runner's decision logic, and
    ``reads`` lets a test assert that a dry run touched evidence at all (and a refused real
    run touched nothing else).
    """

    m04_scope_state: ScopedEvidenceReadiness = field(
        default_factory=lambda: ScopedEvidenceReadiness(
            present=True,
            accepted=True,
            outcome="ready",
            writer_generation=GENERATION,
        )
    )
    marker: Any = None
    promotion: PromotionRecord | None = None
    m06_state: ScopedEvidenceReadiness = field(
        default_factory=lambda: ScopedEvidenceReadiness(
            present=True,
            accepted=True,
            outcome="promotable",
            writer_generation=GENERATION,
        )
    )
    reads: list[str] = field(default_factory=list)

    async def m04_scope(self, *, key: PartitionChainKey) -> ScopedEvidenceReadiness:
        self.reads.append("m04_scope")
        return self.m04_scope_state

    async def latest_handoff_marker(self, *, key: PartitionChainKey) -> object | None:
        self.reads.append("latest_handoff_marker")
        return self.marker

    async def m05_promotion(self, *, key: PartitionChainKey) -> PromotionRecord | None:
        self.reads.append("m05_promotion")
        return self.promotion

    async def m06_promotion(self, *, key: PartitionChainKey) -> ScopedEvidenceReadiness:
        self.reads.append("m06_promotion")
        return self.m06_state


class RecordingStageService:
    """A stage service that records its invocations and answers a fixed value."""

    def __init__(self, *, result: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result

    async def _record(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return self._result

    async def resume_checkpoint(self, **kwargs: Any) -> Any:
        return await self._record(**kwargs)

    async def run_page(self, **kwargs: Any) -> Any:
        return await self._record(**kwargs)

    async def run(self, **kwargs: Any) -> Any:
        return await self._record(**kwargs)

    async def promote(self, **kwargs: Any) -> Any:
        return await self._record(**kwargs)


def replace_stage_service(
    monkeypatch: Any,
    chain_builder: Any,
    *,
    stage: MigrationStage,
    symbol: str,
    service: Any,
) -> None:
    """Swap one stage's executable service, keeping every other stage as assembled.

    ``partition_runner`` imports ``assemble_migration_chain`` into its own namespace, so
    patching it there keeps the real composition root in force for the rest of the chain --
    only the single stage under test gets a service whose calls the test can count.
    """

    def wrapper(dependencies: Any) -> MigrationChain:
        chain = chain_builder(dependencies)
        stages = tuple(
            StageAssembly(stage=assembly.stage, services=((symbol, service),), blockers=())
            if assembly.stage is stage
            else assembly
            for assembly in chain.stages
        )
        return MigrationChain(stages=stages)

    monkeypatch.setattr("prodavan.application.migration.partition_runner."
                        "assemble_migration_chain", wrapper)


__all__ = [
    "CANONICAL_DIGEST",
    "DEFINITION",
    "GENERATION",
    "Inert",
    "MarkerStub",
    "NOW_TEXT",
    "OTHER_DEFINITION",
    "OTHER_TENANT",
    "PrincipalResolver",
    "RecordingEvidenceSource",
    "RecordingSessionFactory",
    "RecordingStageService",
    "ROWS_DIGEST",
    "SNAPSHOT_DIGEST",
    "TENANT",
    "complete_overrides",
    "key",
    "other_key",
    "promotion_record",
    "ready_marker",
    "replace_stage_service",
    "runtime_context",
    "shadow_ports",
]
