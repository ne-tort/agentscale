"""Operator entry point: run **one** migration stage for **one** partition.

Task ``MIG-PARTITION-ASSEMBLY-01``.

Shape of the operation
======================

One explicit call performs exactly one stage of exactly one partition::

    run_partition_stage(key=..., stage=MigrationStage.M04_SCOPE, session_factory=...,
                        writer_generation=3)                     # dry run (default)

There is no loop over partitions, no scheduler, no queue and no timer anywhere in this
module: nothing here can advance a partition the caller did not name.  That is deliberate --
an operator states the partition and the stage, reads the outcome, then states the next one.

Dry run is the default
======================

``dry_run=True`` builds the partition's dependency set, assembles the chain through the
reviewed composition root and evaluates every precondition that is readable from durable
evidence, returning a :class:`PartitionStageOutcome`.  It **executes nothing**: no backfill
page, no import, no parity run, no cutover transition, no write.

``dry_run=False`` is the only path that reaches a stage service, and it is reached only when

1. the partition's dependency set is complete (no
   :class:`~prodavan.application.migration.partition_chain.PartitionChainBlocker`), **and**
2. the stage has an executable service in the assembled chain, **and**
3. the partition's own durable evidence proves the precondition of that stage.

Otherwise nothing runs and the outcome carries ``partition_execution_refused`` plus every
specific reason.

Fail-closed precondition ladder
===============================

Each stage's precondition is read from a **registered** table of exactly this partition; a
stage whose earlier stage has not recorded its evidence refuses.

==========================  ============================================================  ========================================
stage                       durable precondition                                            read from
==========================  ============================================================  ========================================
``M04_SCOPE``               none: it is the first stage, and the scope resolver re-proves   (nothing to read -- the
                            the owner/RLS gates per row at run time                          check records ``ok``)
``M04E_DEFINITION_VERSION`` a ``ready`` M04-02 scope row for the partition's tenant at     ``m04_scope_promotion_evidence``
                            this writer generation
``M05_INSTANCE_RUNTIME``    a ``ready`` ``MIG-M04E-02`` handoff marker for this partition    ``m04e_definition_version_
                            at this generation with zero quarantined rows                    promotion_evidence``
``M06_GRANTS_GATEWAY``      a ``ready`` M05 promotion record for this partition at this     ``m05_plugin_instance_promotion_
                            generation                                                       evidence``
``CUTOVER``                 a ``ready``/``promotable`` M06 promotion record for this         ``m06_canonical_grant_promotion_
                            partition at this writer epoch                                   evidence``
==========================  ============================================================  ========================================

Codes, most specific first: the refusal class is never depended on alone, so an operator log
still says exactly which artefact was absent (``partition_stage_precondition_unmet``),
unaccepted (``partition_evidence_missing``), stale (``partition_writer_generation_stale``) or
non-zero (``partition_unresolved_counts``).  A store that cannot be read at all -- an outage,
a missing relation -- is reported as ``partition_evidence_blocked`` with the exception
*class*: an unreadable store is not a negative answer, and it must never read as readiness.

Why no M04E *checkpoint* precondition
=====================================

The M04E backfill's cursor lives inside the redacted ``gate_evidence`` envelope of its
append-only promotion row (``m04e_ports.EVIDENCE_STORE_SCHEMA_GAPS``:
``m04e_backfill_checkpoint_ledger_missing``), which is why ``build_m04e_evidence_store``
returns ``None`` instead of a store.  A first M04E page legitimately has no checkpoint, and
decoding the cursor out of JSON here would be an unreviewed reader.  So the M04E stage is
gated on its *upstream* stage (M04 scope evidence), not on its own cursor, and the cursor is
the caller's ``stage_kwargs["checkpoint"]``.

Idempotency
===========

A repeated call with the same ``(key, stage, writer_generation)`` performs no second write:

* the precondition reads are pure, so the verdict is identical;
* every write port under this module is append-only and replays an exact repeat
  (``SqlM05HandoffEvidenceStore``, ``SqlM05PromotionEvidenceStore``), and
  ``CutoverCommandService`` refuses a stale lease rather than repeating a transition;
* :attr:`PartitionStageOutcome.digest` is computed from the verdict itself (key, stage,
  generation, dry-run flag, executable flag, ordered codes), so an identical repeat is
  byte-identical and a changed generation or verdict is not.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from prodavan.application.migration.composition import (
    MigrationChain,
    MigrationStage,
    StageAssembly,
    assemble_migration_chain,
    stage_spec,
)
from prodavan.application.migration.evidence_ports import (
    M04E_HANDOFF_SCHEMA,
    MigrationEvidenceBlocked,
    SqlM05HandoffEvidenceStore,
    SqlM05PromotionEvidenceStore,
    TrustedEvidenceScope,
    bind_evidence_transaction,
    decode_marker_row,
    digest as evidence_digest,
    is_sha256,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    HandoffStatus as M04EHandoffStatus,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    HandoffStatus as M05HandoffStatus,
    PromotionRecord,
)
from prodavan.application.migration.partition_chain import (
    PartitionAssemblyOverrides,
    PartitionChainKey,
    PartitionChainPlan,
    build_partition_chain,
)
from prodavan.infrastructure.persistence.models.canonical_grants import (
    M06CanonicalGrantPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
    M04ScopePromotionEvidenceRow,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    SessionFactory = async_sessionmaker[AsyncSession]

# ---------------------------------------------------------------------------
# Refusal vocabulary (stable codes: an operator greps for these, not for prose)
# ---------------------------------------------------------------------------

#: A real run was requested but at least one precondition of the stage failed.
PARTITION_EXECUTION_REFUSED = "partition_execution_refused"
#: An earlier stage of this partition has not recorded its accepted evidence.
PARTITION_STAGE_PRECONDITION_UNMET = "partition_stage_precondition_unmet"
#: The evidence the stage requires exists but does not record acceptance.
PARTITION_EVIDENCE_MISSING = "partition_evidence_missing"
#: The stored evidence belongs to another writer generation/epoch.
PARTITION_WRITER_GENERATION_STALE = "partition_writer_generation_stale"
#: The partition's stored evidence reports unresolved/quarantined/conflicting work.
PARTITION_UNRESOLVED_COUNTS = "partition_unresolved_counts"
#: No evidence source was supplied, so no precondition could be established.
PARTITION_EVIDENCE_READER_UNAVAILABLE = "partition_evidence_reader_unavailable"
#: The evidence boundary refused while being read (it raises rather than guessing).
PARTITION_EVIDENCE_BLOCKED = "partition_evidence_blocked"
#: The stage ran and the service itself refused.
PARTITION_STAGE_REFUSED = "partition_stage_refused"
#: The stage ran and raised; nothing may be reported as applied.
PARTITION_STAGE_FAILED = "partition_stage_failed"


# ---------------------------------------------------------------------------
# Read models: what the operator can actually see
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopedEvidenceReadiness:
    """The state of one accepted-evidence artefact of a partition, as stored.

    ``present`` is "a row exists"; ``accepted`` is "that row records acceptance"; they are
    separate because a blocked/quarantined row is *evidence of a refusal* and must not read
    as "nothing happened yet".
    """

    present: bool = False
    accepted: bool = False
    outcome: str | None = None
    writer_generation: int | None = None
    unresolved: int = 0
    quarantined: int = 0
    conflicts: int = 0

    @property
    def clean(self) -> bool:
        return self.unresolved == 0 and self.quarantined == 0 and self.conflicts == 0


class PartitionEvidenceSource(Protocol):
    """Read-only view of one partition's stored migration evidence.

    Three reads, one per link of the chain.  An implementation must be read-only: the runner
    never writes through this protocol, and every read is for exactly one partition.
    """

    async def m04_scope(
        self, *, key: PartitionChainKey
    ) -> ScopedEvidenceReadiness: ...

    async def latest_handoff_marker(self, *, key: PartitionChainKey) -> object | None: ...

    async def m05_promotion(self, *, key: PartitionChainKey) -> PromotionRecord | None: ...

    async def m06_promotion(
        self, *, key: PartitionChainKey
    ) -> ScopedEvidenceReadiness: ...


class SqlPartitionEvidenceSource:
    """Default reader over the registered, partition-bound evidence tables.

    Every read is tenant-bound by the same :class:`TrustedEvidenceScope` the chain writes
    with, so an operator reads exactly the rows the migration would write -- and a row of
    another tenant is invisible even if the query forgot a predicate.

    The M04E handoff marker is read through ``decode_marker_row`` -- the *same decoder* the
    M04E store writes with -- so an operator and a runtime can never disagree about what a
    stored marker says.  A store refusal is caught by the runner and reported as
    ``partition_evidence_blocked``.

    Every read opens its own transaction and applies the trusted evidence scope first
    (``bind_evidence_transaction``), exactly like the write ports: an RLS-bound table must not
    be read under an unset tenant GUC, and a read that leaked across tenants would defeat the
    point of a per-partition operator entry point.
    """

    def __init__(self, *, session_factory: "SessionFactory", scope: TrustedEvidenceScope) -> None:
        if session_factory is None:
            raise ValueError("the partition evidence source needs a session factory")
        if not isinstance(scope, TrustedEvidenceScope) or not scope.usable:
            raise ValueError("the partition evidence source needs a trusted evidence scope")
        self._session_factory = session_factory
        self._scope = scope
        self._handoff = SqlM05HandoffEvidenceStore(
            session_factory=session_factory, scope=scope
        )
        self._promotion = SqlM05PromotionEvidenceStore(
            session_factory=session_factory, scope=scope
        )

    @property
    def scope(self) -> TrustedEvidenceScope:
        return self._scope

    @asynccontextmanager
    async def _read(self) -> AsyncIterator[AsyncSession]:
        """One tenant-bound read transaction; the scope is proven before any query."""

        async with self._session_factory() as session:
            async with session.begin():
                await bind_evidence_transaction(session, self._scope)
                yield session

    async def m04_scope(self, *, key: PartitionChainKey) -> ScopedEvidenceReadiness:
        """The newest M04-02 row for the partition's *tenant* scope.

        ``m04_scope_promotion_evidence`` is keyed ``(tenant_id, scope_kind, scope_id)``: the
        M04 backfill resolves typed owner scopes, so the tenant-scope row is the one row that
        attests "this tenant's owner registry was accepted" for every definition in it.
        """
        async with self._read() as session:
            row = await session.scalar(
                select(M04ScopePromotionEvidenceRow)
                .where(
                    M04ScopePromotionEvidenceRow.tenant_id == key.tenant_id,
                    M04ScopePromotionEvidenceRow.scope_kind == "tenant",
                    M04ScopePromotionEvidenceRow.scope_id == key.tenant_id,
                )
                .order_by(
                    M04ScopePromotionEvidenceRow.recorded_at.desc(),
                    M04ScopePromotionEvidenceRow.id.desc(),
                )
                .limit(1)
            )
        if row is None:
            return ScopedEvidenceReadiness()
        return ScopedEvidenceReadiness(
            present=True,
            accepted=str(row.outcome) == "ready",
            outcome=str(row.outcome),
            writer_generation=int(row.writer_generation) if row.writer_generation is not None else None,
        )

    async def latest_handoff_marker(self, *, key: PartitionChainKey) -> object | None:
        """The partition's newest ``MIG-M04E-02`` marker, or ``None``.

        Read is by partition and schema tag, **not** by export digest: the operator's question
        is "has M04E recorded its handoff for this partition", and the digest is what the
        marker itself reports -- requiring the digest up front would mean refusing precisely
        when the marker is the only artefact that could supply it.
        """
        async with self._read() as session:
            row = await session.scalar(
                select(M04EDefinitionVersionPromotionEvidenceRow)
                .where(
                    M04EDefinitionVersionPromotionEvidenceRow.tenant_id == key.tenant_id,
                    M04EDefinitionVersionPromotionEvidenceRow.definition_id
                    == key.app_definition_id,
                    M04EDefinitionVersionPromotionEvidenceRow.gate_evidence["schema"].astext
                    == M04E_HANDOFF_SCHEMA,
                )
                .order_by(
                    M04EDefinitionVersionPromotionEvidenceRow.recorded_at.desc(),
                    M04EDefinitionVersionPromotionEvidenceRow.id.desc(),
                )
                .limit(1)
            )
        return decode_marker_row(row)

    async def m05_promotion(self, *, key: PartitionChainKey) -> PromotionRecord | None:
        return await self._promotion.load_latest(
            tenant_id=key.tenant_id, app_definition_id=key.app_definition_id
        )

    async def m06_promotion(self, *, key: PartitionChainKey) -> ScopedEvidenceReadiness:
        """The newest M06 promotion row for ``(tenant, definition)``.

        ``m06_canonical_grant_promotion_evidence`` is keyed by the *four-part* partition
        (tenant, definition, instance); a definition can hold several instances, so the read
        is per definition and reports the newest row's outcome and writer epoch.  A single
        blocked instance therefore still shows up here -- it just does not read as accepted.
        """
        async with self._read() as session:
            rows = (
                await session.execute(
                    select(M06CanonicalGrantPromotionEvidenceRow)
                    .where(
                        M06CanonicalGrantPromotionEvidenceRow.tenant_id == key.tenant_id,
                        M06CanonicalGrantPromotionEvidenceRow.definition_id
                        == key.app_definition_id,
                    )
                    .order_by(
                        M06CanonicalGrantPromotionEvidenceRow.recorded_at.desc(),
                        M06CanonicalGrantPromotionEvidenceRow.id.desc(),
                    )
                )
            ).scalars().all()
        if not rows:
            return ScopedEvidenceReadiness()
        newest = rows[0]
        outcome = str(newest.outcome)
        epoch = newest.writer_epoch
        return ScopedEvidenceReadiness(
            present=True,
            accepted=outcome in {"ready", "promotable"},
            outcome=outcome,
            writer_generation=None if epoch is None else int(epoch),
        )


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageExecution:
    """What a real run handed to the stage, and what it answered.

    ``result`` is the service's own return value (an ``ImportBatch``, an ``M05EvidenceExport``,
    a ``ParityRunReport``, ...).  The runner forwards it; it does not reinterpret it.
    """

    service: str
    method: str
    result: object


@dataclass(frozen=True, slots=True)
class PartitionStageOutcome:
    """The verdict of one call: what was planned, what was refused, what ran.

    JSON-safe through :meth:`snapshot` (names and codes only -- never an object, a payload or
    a DSN).  :attr:`digest` is a deterministic function of the verdict, so an identical repeat
    returns an identical digest and a changed verdict does not.
    """

    key: PartitionChainKey
    stage: MigrationStage
    writer_generation: int
    dry_run: bool
    #: The stage may run: dependency set complete, service present, preconditions hold.
    executable: bool
    #: Codes, most specific first: refusal class first for a real run, then each reason.
    blocker_codes: tuple[str, ...] = ()
    #: One human-readable reason per code, same order, for operator logs.
    details: tuple[str, ...] = ()
    #: Services the stage assembly constructed (symbol names).
    services: tuple[str, ...] = ()
    #: Partition-scope dependencies that were constructed (evidence stores included).
    built: tuple[str, ...] = ()
    #: The stage's own assembly blockers, as the composition root reports them.
    stage_blocker_codes: tuple[str, ...] = ()
    #: Names of the checks the ladder evaluated, in order (``name:verdict``).
    checks: tuple[str, ...] = ()
    execution: StageExecution | None = None

    @property
    def executed(self) -> bool:
        return self.execution is not None

    @property
    def refused(self) -> bool:
        return bool(self.blocker_codes)

    def blocked(self, code: str) -> bool:
        return code in self.blocker_codes

    @property
    def digest(self) -> str:
        """Deterministic identity of this verdict (same input -> same digest)."""

        return evidence_digest({
            "tenant_id": self.key.tenant_id,
            "app_definition_id": self.key.app_definition_id,
            "stage": self.stage.value,
            "writer_generation": self.writer_generation,
            "dry_run": self.dry_run,
            "executable": self.executable,
            "blocker_codes": list(self.blocker_codes),
            "stage_blocker_codes": list(self.stage_blocker_codes),
        })

    def snapshot(self) -> dict[str, object]:
        return {
            "tenant_id": self.key.tenant_id,
            "app_definition_id": self.key.app_definition_id,
            "stage": self.stage.value,
            "writer_generation": self.writer_generation,
            "dry_run": self.dry_run,
            "executable": self.executable,
            "executed": self.executed,
            "blocker_codes": list(self.blocker_codes),
            "details": list(self.details),
            "services": list(self.services),
            "built": list(self.built),
            "stage_blocker_codes": list(self.stage_blocker_codes),
            "checks": list(self.checks),
            "execution": (
                None
                if self.execution is None
                else {"service": self.execution.service, "method": self.execution.method}
            ),
            "digest": self.digest,
        }


#: A dry run *is* a plan; callers often want that name for the diagnostic artefact.
PartitionStagePlan = PartitionStageOutcome


# ---------------------------------------------------------------------------
# Stage service table
# ---------------------------------------------------------------------------

#: ``stage -> (service symbol in the assembly, method a real run calls)``.  Kept as data so
#: exactly one place knows how to start a stage, and so a test can assert the mapping rather
#: than trust prose.
STAGE_EXECUTION: dict[MigrationStage, tuple[str, str]] = {
    MigrationStage.M04_SCOPE: ("scope_migration_coordinator", "resume_checkpoint"),
    MigrationStage.M04E_DEFINITION_VERSION: ("evidence_backfill", "run_page"),
    MigrationStage.M05_INSTANCE_RUNTIME: ("import_service", "run_page"),
    MigrationStage.M06_GRANTS_GATEWAY: ("parity_runner", "run"),
    MigrationStage.CUTOVER: ("cutover_command_service", "promote"),
}


def _stage_service(
    assembly: StageAssembly, stage: MigrationStage
) -> tuple[str, object] | None:
    """The executable service of ``stage``, or ``None`` when the stage has none."""

    symbol, method = STAGE_EXECUTION[stage]
    instance = assembly.service(symbol)
    if instance is None or not callable(getattr(instance, method, None)):
        return None
    return symbol, instance


# ---------------------------------------------------------------------------
# Precondition ladder -- one async check per stage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Refusal:
    code: str
    detail: str


def _no_source() -> _Refusal:
    return _Refusal(
        PARTITION_EVIDENCE_READER_UNAVAILABLE,
        "no partition evidence source was supplied, so no stored evidence of this partition "
        "could be read and the stage cannot be treated as runnable",
    )


async def _check_m04(
    source: PartitionEvidenceSource | None, plan: PartitionChainPlan
) -> _Refusal | None:
    """M04 is the first stage: there is no earlier stage to wait for.

    The scope resolver re-proves the owner/RLS gates per row inside the M04 transaction, so a
    static precondition would add nothing a durable row could attest.  A missing source is
    therefore not a refusal here -- it only means there was nothing to read.
    """

    return None


async def _check_m04e(
    source: PartitionEvidenceSource | None, plan: PartitionChainPlan
) -> _Refusal | None:
    """M04E may start only once the partition's tenant has accepted M04-02 scope evidence."""

    if source is None:
        return _no_source()
    try:
        readiness = await source.m04_scope(key=plan.key)
    except MigrationEvidenceBlocked as exc:
        return _Refusal(PARTITION_EVIDENCE_BLOCKED, ", ".join(exc.blockers))
    if not readiness.present:
        return _Refusal(
            PARTITION_STAGE_PRECONDITION_UNMET,
            f"no M04-02 scope evidence row for tenant {plan.key.tenant_id!r}: the M04 stage "
            "must record its accepted scope/RLS evidence before M04E can read a page",
        )
    if not readiness.accepted:
        return _Refusal(
            PARTITION_EVIDENCE_MISSING,
            f"the stored M04-02 scope evidence reads {readiness.outcome!r}, not 'ready': a "
            "blocked/quarantined row records a refusal and never acceptance",
        )
    if not readiness.clean:
        return _Refusal(
            PARTITION_UNRESOLVED_COUNTS,
            f"the stored M04 evidence still reports {readiness.unresolved} unresolved, "
            f"{readiness.quarantined} quarantined and {readiness.conflicts} conflicting rows",
        )
    if readiness.writer_generation != plan.writer_generation:
        return _Refusal(
            PARTITION_WRITER_GENERATION_STALE,
            f"the stored M04-02 evidence is at writer generation "
            f"{readiness.writer_generation!r}, not {plan.writer_generation!r}",
        )
    return None


async def _check_m05(
    source: PartitionEvidenceSource | None, plan: PartitionChainPlan
) -> _Refusal | None:
    """M05 may start only once the M04E handoff marker for this partition reads ``ready``.

    The marker is read by partition, not by export digest: the digest is precisely what the
    marker attests, so demanding it up front would refuse exactly when the marker is the only
    artefact that could supply it.  ``M05UpstreamEvidence`` is *not* a precondition here --
    it is a chain dependency, and its absence is already named by the planner.
    """

    if source is None:
        return _no_source()
    try:
        marker = await source.latest_handoff_marker(key=plan.key)
    except MigrationEvidenceBlocked as exc:
        return _Refusal(PARTITION_EVIDENCE_BLOCKED, ", ".join(exc.blockers))
    if marker is None:
        return _Refusal(
            PARTITION_STAGE_PRECONDITION_UNMET,
            "no MIG-M04E-02 handoff marker for this partition: the M04E stage must record "
            "one before M05 may import",
        )
    digest_of_marker = getattr(marker, "canonical_digest", None)
    if not isinstance(digest_of_marker, str) or not is_sha256(digest_of_marker):
        return _Refusal(
            PARTITION_EVIDENCE_MISSING,
            "the stored handoff marker carries no usable canonical digest, so it cannot bind "
            "an import",
        )
    status = getattr(marker, "status", None)
    if status is not M04EHandoffStatus.READY:
        return _Refusal(
            PARTITION_EVIDENCE_MISSING,
            f"the stored handoff marker reads {getattr(status, 'value', status)!r}, not "
            "'ready': a blocked marker records a refusal and never acceptance",
        )
    quarantined = int(getattr(marker, "quarantined_count", 0) or 0)
    if quarantined:
        return _Refusal(
            PARTITION_UNRESOLVED_COUNTS,
            f"the stored handoff marker carries {quarantined} quarantined M04E rows, so the "
            "partition is not clean",
        )
    generation = getattr(marker, "writer_generation", None)
    if generation is not None and generation != plan.writer_generation:
        return _Refusal(
            PARTITION_WRITER_GENERATION_STALE,
            f"the stored handoff marker is at writer generation {generation!r}, not "
            f"{plan.writer_generation!r}",
        )
    return None


async def _check_m06(
    source: PartitionEvidenceSource | None, plan: PartitionChainPlan
) -> _Refusal | None:
    """M06 may start only once the M05 promotion record for this partition reads ``ready``."""

    if source is None:
        return _no_source()
    try:
        record = await source.m05_promotion(key=plan.key)
    except MigrationEvidenceBlocked as exc:
        return _Refusal(PARTITION_EVIDENCE_BLOCKED, ", ".join(exc.blockers))
    if record is None:
        return _Refusal(
            PARTITION_STAGE_PRECONDITION_UNMET,
            "no M05 promotion record for this partition: M05 must record its handoff before "
            "M06 may shadow it",
        )
    if record.partition != plan.key.partition:
        return _Refusal(
            PARTITION_EVIDENCE_MISSING,
            "the stored M05 promotion record belongs to another partition",
        )
    if record.status is not M05HandoffStatus.READY or (
        record.m06_handoff is not M05HandoffStatus.READY
    ):
        return _Refusal(
            PARTITION_EVIDENCE_MISSING,
            f"the stored M05 promotion record reads {record.status.value!r} with m06_handoff "
            f"{record.m06_handoff.value!r}, not 'ready'",
        )
    if record.quarantined_count or record.conflict_count:
        return _Refusal(
            PARTITION_UNRESOLVED_COUNTS,
            f"{record.quarantined_count} quarantined and {record.conflict_count} conflicting "
            "instances are outstanding for this partition",
        )
    if record.writer_generation != plan.writer_generation:
        return _Refusal(
            PARTITION_WRITER_GENERATION_STALE,
            f"the stored M05 promotion record is at writer generation "
            f"{record.writer_generation!r}, not {plan.writer_generation!r}",
        )
    return None


async def _check_cutover(
    source: PartitionEvidenceSource | None, plan: PartitionChainPlan
) -> _Refusal | None:
    """Cutover may start only once the M06 promotion record for this partition is accepted."""

    if source is None:
        return _no_source()
    try:
        readiness = await source.m06_promotion(key=plan.key)
    except MigrationEvidenceBlocked as exc:
        return _Refusal(PARTITION_EVIDENCE_BLOCKED, ", ".join(exc.blockers))
    if not readiness.present:
        return _Refusal(
            PARTITION_STAGE_PRECONDITION_UNMET,
            "no M06 promotion record for this partition: cutover requires the M06 stage's "
            "accepted evidence",
        )
    if not readiness.accepted:
        return _Refusal(
            PARTITION_EVIDENCE_MISSING,
            f"the stored M06 promotion record reads {readiness.outcome!r}, not "
            "'ready'/'promotable'",
        )
    if not readiness.clean:
        return _Refusal(
            PARTITION_UNRESOLVED_COUNTS,
            f"the stored M06 evidence still reports {readiness.unresolved} unresolved, "
            f"{readiness.quarantined} quarantined and {readiness.conflicts} conflicting rows",
        )
    if readiness.writer_generation != plan.writer_generation:
        return _Refusal(
            PARTITION_WRITER_GENERATION_STALE,
            f"the stored M06 promotion record is at writer epoch "
            f"{readiness.writer_generation!r}, not {plan.writer_generation!r}",
        )
    return None


_CheckFn = Callable[
    [PartitionEvidenceSource | None, PartitionChainPlan], Awaitable[_Refusal | None]
]

_CHECK_BY_STAGE: dict[MigrationStage, _CheckFn] = {
    MigrationStage.M04_SCOPE: _check_m04,
    MigrationStage.M04E_DEFINITION_VERSION: _check_m04e,
    MigrationStage.M05_INSTANCE_RUNTIME: _check_m05,
    MigrationStage.M06_GRANTS_GATEWAY: _check_m06,
    MigrationStage.CUTOVER: _check_cutover,
}


async def _evaluate(
    *,
    stage: MigrationStage,
    plan: PartitionChainPlan,
    source: PartitionEvidenceSource | None,
    stage_assembly: StageAssembly,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return ``(codes, details, checks)`` for the whole ladder, in order."""

    codes: list[str] = []
    details: list[str] = []
    checks: list[str] = []

    # 1. the partition's dependency set
    checks.append(f"chain_complete:{not plan.blockers}")
    for blocker in plan.blockers:
        codes.append(blocker.code)
        details.append(f"{blocker.dependency}: {blocker.detail}")

    # 2. an executable service in this assembly
    present = _stage_service(stage_assembly, stage) is not None
    checks.append(f"stage_service_present:{present}")
    if not present:
        codes.append(PARTITION_STAGE_PRECONDITION_UNMET)
        codes_of_assembly = ", ".join(stage_assembly.blocker_codes) or "no service was built"
        details.append(
            f"the {stage.value} stage has no executable service in this assembly "
            f"({codes_of_assembly})"
        )

    # 3. the durable precondition of this stage -- only when there is anything to run.
    #
    # The ladder is ordered, so it short-circuits: a partition with a missing collaborator
    # has no runnable service, and reading its evidence would cost a database round trip to
    # decide something the object graph already settled.  The check is recorded as skipped
    # rather than as passed, so a report never implies the evidence was verified.
    if codes:
        checks.append(f"precondition:{stage.value}:skipped")
        return tuple(codes), tuple(details), tuple(checks)

    try:
        refusal = await _CHECK_BY_STAGE[stage](source, plan)
    except SQLAlchemyError as exc:
        # An unreadable store is *not* a negative answer and must never read as readiness:
        # the stage is refused with the exception class (never its message, which can carry
        # a DSN) so an outage or a missing table cannot be mistaken for "nothing to do".
        refusal = _Refusal(
            PARTITION_EVIDENCE_BLOCKED,
            f"reading this partition's evidence failed with {type(exc).__name__}: an "
            "unreadable store is never treated as readiness",
        )
    checks.append(f"precondition:{stage.value}:{'refused' if refusal else 'ok'}")
    if refusal is not None:
        codes.append(refusal.code)
        details.append(refusal.detail)

    return tuple(codes), tuple(details), tuple(checks)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_partition_stage(
    *,
    key: PartitionChainKey,
    stage: MigrationStage,
    session_factory: "SessionFactory | None",
    writer_generation: int,
    dry_run: bool = True,
    overrides: PartitionAssemblyOverrides | None = None,
    evidence_source: PartitionEvidenceSource | None = None,
    stage_kwargs: dict[str, object] | None = None,
) -> PartitionStageOutcome:
    """Run **one** stage of **one** partition, or plan it when ``dry_run`` is set.

    ``dry_run=True`` (the default) builds the partition's chain, evaluates every readable
    precondition and executes nothing.  ``dry_run=False`` executes the stage only when the
    dependency set is complete, the stage has a service **and** every precondition holds;
    otherwise it returns the same refusals with ``partition_execution_refused`` in front and
    nothing is executed.

    ``overrides`` states the collaborators a partition cannot derive
    (:class:`~prodavan.application.migration.partition_chain.PartitionAssemblyOverrides`); an
    omitted one is a named blocker, never a substitute.

    ``evidence_source`` reads this partition's stored evidence.  When it is omitted and the
    partition has a trusted evidence scope, :class:`SqlPartitionEvidenceSource` is used over
    the same session factory and the same ``TrustedEvidenceScope`` -- so the operator reads
    exactly the rows the chain writes.

    ``stage_kwargs`` is forwarded verbatim to the stage service (``checkpoint``/``context``
    for M05, ``request``/``context`` for the parity runner, the command fields for cutover).
    The runner does not invent them: a service that refuses a missing argument reports
    ``partition_stage_refused``.

    A caller error (not a refusal) raises ``ValueError`` -- a bad key, an unknown stage or a
    non-boolean ``dry_run`` never reads evidence and never runs anything.
    """

    if not isinstance(key, PartitionChainKey):
        raise ValueError("run_partition_stage requires a PartitionChainKey")
    if not isinstance(stage, MigrationStage):
        raise ValueError(f"{stage!r} is not a MigrationStage")
    if dry_run is not True and dry_run is not False:
        raise ValueError("dry_run must be an explicit bool, not a truthy value")
    if stage_kwargs is not None and not isinstance(stage_kwargs, dict):
        raise ValueError("stage_kwargs must be a dict of keyword arguments")
    stage_spec(stage)  # KeyError when the stage is outside the reviewed sequence

    resolved = overrides if overrides is not None else PartitionAssemblyOverrides()

    plan = build_partition_chain(
        key=key,
        session_factory=session_factory,
        writer_generation=writer_generation,
        **resolved.chain_kwargs(),
    )
    chain: MigrationChain = assemble_migration_chain(plan.dependencies)
    stage_assembly = chain.by_stage(stage)

    source = evidence_source
    if source is None and plan.evidence_scope is not None and session_factory is not None:
        source = SqlPartitionEvidenceSource(
            session_factory=session_factory, scope=plan.evidence_scope
        )

    codes, details, checks = await _evaluate(
        stage=stage, plan=plan, source=source, stage_assembly=stage_assembly
    )
    services = tuple(symbol for symbol, _instance in stage_assembly.services)

    if dry_run:
        return PartitionStageOutcome(
            key=key,
            stage=stage,
            writer_generation=writer_generation,
            dry_run=True,
            executable=not codes,
            blocker_codes=codes,
            details=details,
            services=services,
            built=plan.built,
            stage_blocker_codes=stage_assembly.blocker_codes,
            checks=checks,
        )

    if codes:
        return PartitionStageOutcome(
            key=key,
            stage=stage,
            writer_generation=writer_generation,
            dry_run=False,
            executable=False,
            # Class code first so a caller can test one thing; every reason follows.
            blocker_codes=(PARTITION_EXECUTION_REFUSED, *codes),
            details=(
                f"nothing was executed: {stage.value} for "
                f"{key.tenant_id}/{key.app_definition_id} failed "
                f"{len(codes)} precondition(s)",
                *details,
            ),
            services=services,
            built=plan.built,
            stage_blocker_codes=stage_assembly.blocker_codes,
            checks=checks,
        )

    found = _stage_service(stage_assembly, stage)
    assert found is not None  # guaranteed by the ladder above
    symbol, instance = found
    method_name = STAGE_EXECUTION[stage][1]
    kwargs = dict(stage_kwargs or {})

    def _outcome(**extra: object) -> PartitionStageOutcome:
        return PartitionStageOutcome(
            key=key,
            stage=stage,
            writer_generation=writer_generation,
            dry_run=False,
            executable=True,
            services=services,
            built=plan.built,
            stage_blocker_codes=stage_assembly.blocker_codes,
            checks=checks,
            **extra,  # type: ignore[arg-type]
        )

    try:
        result = await getattr(instance, method_name)(**kwargs)
    except MigrationEvidenceBlocked as exc:
        return _outcome(
            blocker_codes=(PARTITION_STAGE_REFUSED,),
            details=(f"{symbol}.{method_name} refused: {', '.join(exc.blockers)}",),
        )
    except Exception as exc:  # noqa: BLE001 - a stage failure is data for the operator
        # The exception *class* only: a message can carry a DSN or stored row content.
        return _outcome(
            blocker_codes=(PARTITION_STAGE_FAILED,),
            details=(f"{symbol}.{method_name} raised {type(exc).__name__}",),
        )

    return _outcome(
        blocker_codes=(),
        details=(),
        execution=StageExecution(service=symbol, method=method_name, result=result),
    )


def stage_execution_digest(outcome: PartitionStageOutcome) -> str:
    """Stable digest of a serialised attempt (identical input -> identical digest).

    Kept as a function as well as a property so a report can hash an outcome it persisted
    earlier without reconstructing the object.
    """

    encoded = json.dumps(
        outcome.snapshot(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "PARTITION_EVIDENCE_BLOCKED",
    "PARTITION_EVIDENCE_MISSING",
    "PARTITION_EVIDENCE_READER_UNAVAILABLE",
    "PARTITION_EXECUTION_REFUSED",
    "PARTITION_STAGE_FAILED",
    "PARTITION_STAGE_PRECONDITION_UNMET",
    "PARTITION_STAGE_REFUSED",
    "PARTITION_UNRESOLVED_COUNTS",
    "PARTITION_WRITER_GENERATION_STALE",
    "PartitionEvidenceSource",
    "PartitionStageOutcome",
    "PartitionStagePlan",
    "STAGE_EXECUTION",
    "ScopedEvidenceReadiness",
    "SqlPartitionEvidenceSource",
    "StageExecution",
    "run_partition_stage",
    "stage_execution_digest",
]
