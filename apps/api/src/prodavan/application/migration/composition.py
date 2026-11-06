"""Composition root for the M04 -> M04E -> M05 -> M06 -> cutover migration chain.

Task ``MIG-ASSEMBLY-01``. Review companion: ``docs/08-migration/integration-seams-review.md``
(§1-§3, §7). Nothing in this module contains migration business logic: it only *connects*
existing services to the durable adapters that already exist in ``src/prodavan``, and it
refuses to pretend that a stage is wired when one of its trusted ports has no
implementation in this repository.

Two separate things live here:

* **assembly** — :func:`build_m04_scope_stage`, :func:`build_m04e_definition_version_stage`,
  :func:`build_m05_instance_runtime_stage`, :func:`build_m06_grants_gateway_stage`,
  :func:`build_cutover_stage`. Each returns a :class:`StageAssembly`: the constructed
  services plus every reason the stage is *not* a production binding. Missing mandatory
  dependency -> a named :class:`AssemblyBlocker`, never a stub, never a silent ``None``.
* **sequence** — :func:`migration_sequence` returns :class:`StageSpec` records (stage ->
  class -> required ports -> precondition). It is pure data: it runs nothing, opens no
  transaction, and its preconditions can be read and checked statically.

Convenience: :func:`assemble_migration_chain` fans :class:`ChainDependencies` through all
five builders and returns the ordered :class:`MigrationChain`. It still only constructs
objects; no stage method is called.

Stage map (also mirrored by ``MIGRATION_SEQUENCE``)
===================================================

======================  ==================================  ==========================  ======================================
stage                   class                               required ports              precondition
======================  ==================================  ==========================  ======================================
``M04_SCOPE``           ``SqlAlchemyScopeBackfillAdapter``  ``TenantScopeContextPort``  trusted DB transaction (``MigrationEvidenceContext``);
                        (``ResolveScopePort`` +            + ``async_sessionmaker``    M04-01/02 owner-registry RLS review
                        ``TrustedScopeTransactionPort``)                                accepted
``M04E_DEFINITION_      ``DefinitionVersionEvidenceBackfill`` ``CandidateReader``,       M04 active typed scope evidence;
VERSION``               (evidence producer) +               ``EvidenceStore`` (G4),     complete digest-bound version checkpoint;
                        ``ServerDefinitionVersionRepository`` ``UpstreamGatePort`` (G6),  ``MIG-M04-01``/``MIG-M04-02`` digests bound to the
                        (server facade)                     ``CandidateProofPort`` (G6),  same M04 objects M05 will read
                                                            ``ArtifactContentPort``
``M05_INSTANCE_RUNTIME`` ``InstanceImportService`` +        ``M05UpstreamEvidence``;    M04E handoff marker (G1) present for the same partition
                        ``FailClosedResolvePluginInstance`` + ``M05IdentityEvidencePort``; + ``M05RuntimeGateEvidence``; exact instance key
                        ``M05PromotionService``             ``RedactedRuntimeAuditPort``;  evidence; six handoff gate names
                                                            ``TrustedRuntimeContext``
``M06_GRANTS_GATEWAY``  ``CapabilityGatewayService``        ``PartitionHandoffReadPort`` ``AcceptedPartitionEvidence`` for the exact partition
                        (shadow) + ``BoundedParityRunner``  (G3) + legacy/canonical/     + identical ``writer_epoch``; parity reports written
                                                            mapping ports
``CUTOVER``             ``CutoverCommandService``           ``CutoverRepository`` (G5)  M04/M04E/M05 ``GateLineageEvidence`` chain (I6) +
                                                            + four verifiers (G5)       parity report -> ``GateEvidence`` mapper (G5) +
                                                                                        legacy owner marker + writer drain + final delta
======================  ==================================  ==========================  ======================================

``(G1)``-``(G6)`` are the review §3 gaps: ports declared in ``app/**`` with no implementation
anywhere. Each remaining gap surfaces here as a blocker code from
:data:`UNIMPLEMENTED_PORTS`, so the stage is reported as **not** wired while the object graph is
still built with whatever the host passed in (this is what lets tests exercise behaviour without
the composition root lying).

Several ``(G)`` gaps have since been closed in the tree and are no longer listed: both
tenant-context binders (``application/migration/tenant_context.py``) and four evidence ports —
``EvidenceStore``, ``M05HandoffPort``, ``M05PromotionRecordPort``, ``PartitionHandoffReadPort``
(``application/migration/evidence_ports.py``). A registry entry for a port the repository *does*
implement is worse than no registry: it would refuse to assemble a graph that actually works.

What still blocks a production binding is a missing **collaborator**, not a missing port — most
notably the auth-boundary ``TrustedPrincipalResolver`` the tenant-context adapters require. Those
absences are reported by the stage builders when the dependency is not supplied.

Two adapters, one name — do not confuse them
--------------------------------------------

``SqlAlchemyScopeBackfillAdapter`` (``m04_scope/scope_persistence.py``) **is** the
``ResolveScopePort``: its ``resolve_active`` returns ``ScopeResolutionEvidence | None`` and is
the only production M04 evidence source for M05/M06. ``ScopeRegistryApplicationAdapter``
(``scope_registry_adapter/adapter.py``) is an *application* adapter that returns
``ScopeAdapterResult`` (outcome + reason codes + evidence ids) for classification/backfill
reporting; it is **not** a port and must never be wired as ``ResolveScopePort``.

Fail-closed rules honoured here
-------------------------------

* No dependency is defaulted: a host must state every port, so an omitted port is a type error
  rather than a deny-everything object graph. Only bounded scalars (``page_size``,
  ``max_command_bytes``) carry defaults, and a test enforces that no default is ``None``. This
  is specifically why ``build_sql_resolve_plugin_instance_port`` is not called with its own
  ``upstream=None`` default: the chain passes ``M05UpstreamEvidence`` explicitly and blocks
  when it is absent.
* Nothing here writes, migrates, or enables canonical positive writes. Every assembled
  M06 object keeps ``DECISION_OWNER == "legacy"``.
* The sequence is validated at import (:func:`validate_migration_sequence`): duplicate stages,
  forward references, and evidence preconditions naming a stage that does not produce them all
  raise immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Callable, Mapping

from prodavan.application.migration.m04_scope.scope_migration import (
    ScopeMigrationCoordinator,
)
from prodavan.application.migration.m04_scope.scope_persistence import (
    SqlAlchemyScopeBackfillAdapter,
    TenantScopeContextPort,
)
from prodavan.application.migration.m04e_definition_version.artifact import ArtifactContentPort
from prodavan.application.migration.m04e_definition_version.backfill import (
    CandidateProofPort,
    CandidateReader,
    DefinitionVersionEvidenceBackfill,
    EvidenceStore,
    M05HandoffPort,
    UpstreamGatePort,
)
from prodavan.application.migration.m04e_definition_version.repository import (
    ServerDefinitionVersionRepository,
)
from prodavan.application.migration.m05_instance_runtime.instance_persistence import (
    RuntimeSessionOwnershipPort,
    SqlAlchemyInstanceImportStore,
    SqlAlchemyM05IdentityEvidence,
    SqlAlchemyM05UnitOfWork,
    SqlAlchemyPinnedSnapshotReader,
    SqlAlchemyRedactedAuditSink,
    TrustedTenantContextPort,
    build_sql_resolve_plugin_instance_port,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    InstanceImportService,
    M05PromotionRecordPort,
    M05PromotionService,
    M05UpstreamEvidence,
    TrustedRuntimeContext,
)
from prodavan.application.migration.m06_cutover.cutover import (
    CanonicalRepairVerifier,
    CutoverCommandService,
    CutoverRepository,
    GateVerifier,
    LegacyOwnerVerifier,
    RollbackVerifier,
)
from prodavan.application.migration.m06_grants_gateway.adapter import (
    CapabilityGatewayService,
    EvidenceReadPort,
    TrustedEvidenceTransactionPort,
)
from prodavan.application.migration.m06_grants_gateway.composition import (
    ShadowGatewayPorts,
    build_parity_runner,
    build_shadow_gateway,
)
from prodavan.application.migration.m06_grants_gateway.evidence_store import (
    SqlCanonicalEvidenceStore,
    SqlParityEvidenceSummaryReader,
)
from prodavan.application.migration.m06_grants_gateway.parity_store import (
    SqlParityReportStore,
)
from prodavan.application.migration.m06_grants_gateway.parity_runner import (
    BoundedParityRunner,
    ParityEvidenceSummaryPort,
    ParityReportStorePort,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    SessionFactory = async_sessionmaker[AsyncSession]


class MigrationStage(StrEnum):
    """Ordered steps of the migration chain; the value is the reviewed stage name."""

    M04_SCOPE = "M04_scope"
    M04E_DEFINITION_VERSION = "M04E_definition_version"
    M05_INSTANCE_RUNTIME = "M05_runtime"
    M06_GRANTS_GATEWAY = "M06_grants_gateway"
    CUTOVER = "cutover"


# ---------------------------------------------------------------------------
# Blocker vocabulary
# ---------------------------------------------------------------------------

#: Assembly-time blockers raised by this module. They are deliberately distinct from the
#: runtime reason codes inside the services: these describe a *missing dependency in the
#: object graph*, not a refused operation.
M04_SESSION_FACTORY_MISSING = "m04_session_factory_missing"

M04E_ARTIFACT_CONTENT_PORT_MISSING = "m04e_artifact_content_port_missing"
M04E_SERVER_REPOSITORY_MISSING = "m04e_server_definition_repository_missing"

M05_RUNTIME_CONTEXT_MISSING = "m05_runtime_context_missing"
M05_UPSTREAM_EVIDENCE_MISSING = "m05_upstream_evidence_missing"
M05_SESSION_FACTORY_MISSING = "m05_session_factory_missing"

M06_LEGACY_SCOPE_AND_QUOTA_REQUIRED = "m06_legacy_scope_and_quota_ports_required"
M06_SESSION_FACTORY_MISSING = "m06_session_factory_missing"
M06_PARITY_OBSERVATIONS_MISSING = "m06_parity_observations_port_missing"
M06_PARITY_SUMMARIES_MISSING = "m06_parity_summaries_port_missing"
M06_PARITY_REPORT_STORE_MISSING = "m06_parity_report_store_missing"

CUTOVER_MAX_COMMAND_BYTES_INVALID = "cutover_max_command_bytes_invalid"


@dataclass(frozen=True, slots=True)
class UnimplementedPort:
    """A trusted port that is declared in ``app/**`` but has no implementation in ``src``.

    Review §3. The composition root reports the matching :attr:`blocker_code` for the stage
    that needs the port; deleting an entry is the deliberate act that certifies a new
    production adapter, so this table doubles as the wiring checklist.
    """

    stage: MigrationStage
    port: str
    declared_in: str
    blocker_code: str
    review_ref: str
    detail: str


UNIMPLEMENTED_PORTS: tuple[UnimplementedPort, ...] = (
    # Ports resolved since this registry was written are removed rather than left
    # as stale entries: a stage that reports a blocker for a port the repository
    # *does* implement would refuse to assemble a working graph, which is worse
    # than no registry at all.
    #
    # * ``TenantScopeContextPort`` / ``TrustedTenantContextPort`` →
    #   ``application/migration/tenant_context.py``. Their remaining collaborator
    #   (an auth-boundary ``TrustedPrincipalResolver``) is a missing dependency,
    #   reported by the stage builder, not a missing port.
    # * ``EvidenceStore``, ``M05HandoffPort``, ``M05PromotionRecordPort``,
    #   ``PartitionHandoffReadPort`` → ``application/migration/evidence_ports.py``.
    #   The M04E ``EvidenceStore`` in particular is
    #   ``m04e_ports.SqlM04EEvidenceStore`` over the four ledger tables
    #   (``m04e_definition_version_{mappings,quarantine,checkpoints,audit}``), which
    #   ``build_m04e_evidence_store`` now builds; the codes it still reports are the
    #   M04E-01 promotion-evidence review items and do not block this port.
    #
    # What is still genuinely absent is listed below.
    #
    # Retired from this list after implementation landed (do not re-add):
    #   ``UpstreamGatePort``, ``CandidateProofPort``, ``CandidateReader`` →
    #   ``application/migration/m04e_ports.py``
    #   (SqlM04EUpstreamGate, SqlM04ECandidateProof, SqlM04ECandidateReader).
    #   ``CutoverRepository``, ``GateVerifier``, ``LegacyOwnerVerifier``,
    #   ``RollbackVerifier``, ``CanonicalRepairVerifier`` →
    #   ``application/migration/m06_cutover/ports.py`` (MIG-CUTOVER-PORTS-01:
    #   SqlCutoverRepository + SqlGateVerifier/SqlLegacyOwnerVerifier/
    #   SqlRollbackVerifier/SqlCanonicalRepairVerifier). The repository itself still
    #   refuses until its two tables exist (``CUTOVER_REQUIRED_SCHEMA``), which is a
    #   missing *schema*, reported at call time, not a missing port.
    # The M06 stage builder does not accept the evidence ports yet, so
    # ``PartitionHandoffReadPort`` keeps its entry until that wiring lands; a
    # registry entry for an implemented port would refuse a graph that works.
    #
    # ``PartitionHandoffReadPort`` was the last entry and has now been retired: the M06
    # builder takes it through ``ShadowGatewayPorts.handoff_partitions``, and
    # ``SqlPartitionHandoffReadStore`` implements it. The register is therefore empty,
    # which is the honest state: every port the chain declares now has an implementation
    # and a builder that accepts it. ``_port_blockers`` still reports a port the host
    # omits, so an unwired deployment is named rather than silently degraded.
)

_UNIMPLEMENTED_BY_STAGE: Mapping[MigrationStage, tuple[UnimplementedPort, ...]] = {
    stage: tuple(port for port in UNIMPLEMENTED_PORTS if port.stage is stage)
    for stage in MigrationStage
}


def unimplemented_ports(stage: MigrationStage) -> tuple[UnimplementedPort, ...]:
    """Ports the chain needs for ``stage`` that have no implementation in ``src``."""

    return _UNIMPLEMENTED_BY_STAGE.get(stage, ())


# ---------------------------------------------------------------------------
# Assembly result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssemblyBlocker:
    """One concrete reason a stage is not a production binding."""

    stage: MigrationStage
    code: str
    port: str | None
    detail: str

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        port = f" ({self.port})" if self.port else ""
        return f"{self.stage.value}:{self.code}{port}: {self.detail}"


@dataclass(frozen=True, slots=True)
class StageAssembly:
    """Constructed services for one stage plus every unresolved dependency.

    ``services`` holds ``(symbol, instance)`` pairs so the assembly stays a plain frozen
    value: callers look instances up by the symbol they asked for
    (:meth:`StageAssembly.service`). A stage is :attr:`wired` only when something was built
    *and* no blocker remains — a composition that built objects on top of a port with no
    production implementation is deliberately **not** wired.
    """

    stage: MigrationStage
    services: tuple[tuple[str, object], ...]
    blockers: tuple[AssemblyBlocker, ...]

    def service(self, symbol: str) -> object | None:
        """Return the instance registered as ``symbol``, or ``None`` when it was not built."""

        for name, instance in self.services:
            if name == symbol:
                return instance
        return None

    @property
    def wired(self) -> bool:
        """True only for a complete production object graph with no blocker."""

        return bool(self.services) and not self.blockers

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(blocker.code for blocker in self.blockers)


def _blocker(stage: MigrationStage, code: str, detail: str, *, port: str | None = None) -> AssemblyBlocker:
    return AssemblyBlocker(stage=stage, code=code, port=port, detail=detail)


def _port_blockers(
    stage: MigrationStage, supplied: Mapping[str, object | None]
) -> tuple[AssemblyBlocker, ...]:
    """Every declared-but-unimplemented port of ``stage`` becomes exactly one named blocker.

    The code is the registry code in both cases; only the detail differs, so an acceptance
    table can assert the *set* of codes without tracking how the host happened to call the
    builder:

    * nothing supplied -> the dependency is absent from the object graph;
    * something supplied -> an object graph exists, but it rests on a port that has no
      reviewed production implementation in ``src/prodavan``, so the stage is not wired.
    """

    blockers: list[AssemblyBlocker] = []
    for port in unimplemented_ports(stage):
        wired_by_host = supplied.get(port.port) is not None
        status = (
            "host supplied an object, but src/prodavan has no reviewed implementation"
            if wired_by_host
            else "not supplied to the composition"
        )
        blockers.append(_blocker(
            stage, port.blocker_code,
            f"{port.detail} [{port.review_ref}: {port.declared_in}] ({status})",
            port=port.port,
        ))
    return tuple(blockers)


def _dedupe(blockers: tuple[AssemblyBlocker, ...]) -> tuple[AssemblyBlocker, ...]:
    seen: set[tuple[str, str | None]] = set()
    ordered: list[AssemblyBlocker] = []
    for blocker in blockers:
        key = (blocker.code, blocker.port)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(blocker)
    return tuple(ordered)


#: Refused before construction: both services enforce these bounds themselves, but a
#: composition error should surface as one named blocker instead of a construction traceback.
M04E_PAGE_SIZE_INVALID = "m04e_page_size_invalid"
M05_PAGE_SIZE_INVALID = "m05_page_size_invalid"

#: A mandatory port the host omitted. Once a port leaves :data:`UNIMPLEMENTED_PORTS` (because
#: *this* repository now implements it), the registry can no longer report its absence, so the
#: builder has to: a stage that constructs nothing must still say which dependency is missing.
#: One code per stage; :class:`AssemblyBlocker` carries the port name, so each absent port is
#: its own unnamed-to-nobody blocker and :func:`_dedupe` keeps them distinct.
REQUIRED_PORT_MISSING = {
    MigrationStage.M04_SCOPE: "m04_required_port_missing",
    MigrationStage.M04E_DEFINITION_VERSION: "m04e_required_port_missing",
    MigrationStage.M05_INSTANCE_RUNTIME: "m05_required_port_missing",
    MigrationStage.CUTOVER: "cutover_required_port_missing",
}


def _required_port_blockers(
    stage: MigrationStage, supplied: Mapping[str, object | None]
) -> tuple[AssemblyBlocker, ...]:
    """One named blocker for every mandatory port the host did not supply."""

    code = REQUIRED_PORT_MISSING[stage]
    return tuple(
        _blocker(
            stage, code,
            f"{name} is mandatory for {stage.value}: without it this stage has no evidence "
            "source, so nothing is constructed and the stage is not wired",
            port=name,
        )
        for name, value in supplied.items()
        if value is None
    )


# ---------------------------------------------------------------------------
# M04 — typed scope resolution and backfill
# ---------------------------------------------------------------------------


def build_m04_scope_stage(
    *,
    session_factory: "SessionFactory | None",
    tenant_context: TenantScopeContextPort | None,
) -> StageAssembly:
    """Wire M04 from the durable scope adapter.

    ``SqlAlchemyScopeBackfillAdapter`` **is** the ``ResolveScopePort``: one PostgreSQL
    transaction per page/row, transaction-local RLS through ``TenantScopeContextPort.bind``,
    ``resolve_active`` returning ``ScopeResolutionEvidence | None``. It is not
    ``ScopeRegistryApplicationAdapter`` — that application adapter returns
    ``ScopeAdapterResult`` (outcome/reason/evidence ids) and is not a port at all.

    Assembled services:

    ``resolve_scope_port``
        Consumed by M06 identity (``ShadowGatewayPorts.m04_scope``) and by M04E gate proofs.
    ``trusted_scope_transaction_port``
        Consumed by ``ScopeMigrationCoordinator`` (checkpoint read, mapping, quarantine).
    ``scope_migration_coordinator``
        Exactly one source row per call; no local persistence exists anywhere.

    A missing ``session_factory`` or ``tenant_context`` yields a named blocker. There is no
    in-memory or deny-everything fallback: a scope port that cannot resolve must not exist.

    With both dependencies supplied the stage **is** wired: ``TenantScopeContextPort`` is
    implemented in ``application/migration/tenant_context.py``, so it is no longer a review §3
    registry gap. What still gates production use is the adapter's own ``WIRING.md``: the
    checkpoint/mapping schema lineage and the RLS/append-only privileges must be reviewed
    before this stage is operated, and that review is not something this module can perform.
    """

    stage = MigrationStage.M04_SCOPE
    blockers: list[AssemblyBlocker] = []
    if session_factory is None:
        blockers.append(_blocker(
            stage, M04_SESSION_FACTORY_MISSING,
            "an async_sessionmaker over PostgreSQL is required for transaction-local RLS",
            port="async_sessionmaker",
        ))
    # ``TenantScopeContextPort`` is implemented in tenant_context.py, so the registry no longer
    # reports it; an omitted binder is still a missing dependency and must be named here.
    blockers.extend(_required_port_blockers(stage, {"TenantScopeContextPort": tenant_context}))

    services: list[tuple[str, object]] = []
    if session_factory is not None and tenant_context is not None:
        adapter = SqlAlchemyScopeBackfillAdapter(
            session_factory=session_factory,
            tenant_context=tenant_context,
        )
        services.append(("resolve_scope_port", adapter))
        services.append(("trusted_scope_transaction_port", adapter))
        services.append(("scope_migration_coordinator", ScopeMigrationCoordinator(adapter)))
    return StageAssembly(stage=stage, services=tuple(services), blockers=_dedupe(tuple(blockers)))


# ---------------------------------------------------------------------------
# M04E — definition/version evidence backfill and server resolution
# ---------------------------------------------------------------------------


def build_m04e_definition_version_stage(
    *,
    reader: CandidateReader | None,
    store: EvidenceStore | None,
    gates: UpstreamGatePort | None,
    proofs: CandidateProofPort | None,
    artifacts: ArtifactContentPort | None,
    server_repository: ServerDefinitionVersionRepository | None,
    m05_handoff: M05HandoffPort | None,
    page_size: int = 100,
) -> StageAssembly:
    """Wire M04E: bounded page backfill plus the server-side definition resolver facade.

    ``DefinitionVersionEvidenceBackfill`` refuses to run without all five trusted ports; the
    composition mirrors that contract and reports each absent port separately instead of
    letting the service discover it mid-run. The five ports now have durable implementations
    (``application/migration/m04e_ports.py`` and ``evidence_ports.py``), so they left the
    review §3 registry; an omitted one is reported by this builder as
    ``m04e_required_port_missing`` and the stage is not wired.

    ``store`` is :class:`~prodavan.application.migration.m04e_ports.SqlM04EEvidenceStore`
    over the four ledger tables revision ``2026110315`` created
    (``m04e_definition_version_{mappings,quarantine,checkpoints,audit}``): it appends
    evidence and CASes the cursor. ``build_m04e_evidence_store`` therefore returns a
    *store* rather than ``None``, and the codes it still reports
    (``EVIDENCE_STORE_SCHEMA_GAPS``) are the M04E-01 promotion-evidence review items —
    they hold back the gate, not this stage's port. This builder does not fold them
    into the stage: a stage blocker must name a missing dependency, and the store is
    not missing.

    Raw data supplies ``server_repository`` because
    ``M04EDefinitionVersionPersistenceAdapter`` is bound to a *caller-owned* ``AsyncSession``
    (it must stay inside one SERIALIZABLE transaction), so it cannot be built from a session
    factory here — that is a blocker, not an omission.

    ``m05_handoff`` is registered as ``m05_handoff_port`` for
    ``persist_m05_handoff(..., store=...)``; without it the M04E -> M05 marker cannot be
    produced and the stage refuses to claim it is wired.
    """

    stage = MigrationStage.M04E_DEFINITION_VERSION
    blockers: list[AssemblyBlocker] = list(_required_port_blockers(stage, {
        "CandidateReader": reader,
        "EvidenceStore": store,
        "UpstreamGatePort": gates,
        "CandidateProofPort": proofs,
        "M05HandoffPort": m05_handoff,
    }))
    if artifacts is None:
        blockers.append(_blocker(
            stage, M04E_ARTIFACT_CONTENT_PORT_MISSING,
            "proof-bound artifact content read port is required to bind an immutable version",
            port="ArtifactContentPort",
        ))
    if server_repository is None:
        blockers.append(_blocker(
            stage, M04E_SERVER_REPOSITORY_MISSING,
            "ServerDefinitionVersionRepository must be built by the caller inside its own "
            "SERIALIZABLE transaction (the persistence adapter takes an AsyncSession, not a "
            "factory); M06 identity and definition resolution have no other evidence source",
            port="ServerDefinitionVersionRepository",
        ))
    page_size_valid = 1 <= page_size <= 1000
    if not page_size_valid:
        blockers.append(_blocker(
            stage, M04E_PAGE_SIZE_INVALID,
            f"page_size {page_size!r} is outside the bounded 1..1000 contract",
            port="page_size",
        ))

    services: list[tuple[str, object]] = []
    if store is not None and artifacts is not None and page_size_valid:
        services.append(("evidence_backfill", DefinitionVersionEvidenceBackfill(
            reader, store, gates=gates, proofs=proofs, artifacts=artifacts, page_size=page_size,
        )))
    if server_repository is not None:
        services.append(("server_definition_version_repository", server_repository))
    if m05_handoff is not None:
        services.append(("m05_handoff_port", m05_handoff))
    return StageAssembly(stage=stage, services=tuple(services), blockers=_dedupe(tuple(blockers)))


# ---------------------------------------------------------------------------
# M05 — instance import, exact resolution, promotion record
# ---------------------------------------------------------------------------


def build_m05_instance_runtime_stage(
    *,
    session_factory: "SessionFactory | None",
    tenant_context: TrustedTenantContextPort | None,
    runtime_context: TrustedRuntimeContext | None,
    upstream_evidence: M05UpstreamEvidence | None,
    promotion_records: M05PromotionRecordPort | None,
    identity_sessions: RuntimeSessionOwnershipPort | None,
    page_size: int = 100,
) -> StageAssembly:
    """Wire M05: bounded import, fail-closed exact resolution, promotion handoff.

    ``upstream_evidence`` is mandatory. ``build_sql_resolve_plugin_instance_port`` defaults
    it to ``None``, which silently yields a deny-everything port; the composition never
    relies on that default and blocks when the evidence is absent. The same
    ``M05UpstreamEvidence`` instance is threaded into ``InstanceImportService`` and into the
    resolver so both read identical M04/M04E/M05 digests.

    The import store and snapshot reader are bound to the run's ``TrustedRuntimeContext``
    (they refuse any other tenant), so this stage assembles *one run*, not a global service.

    ``tenant_context`` and ``promotion_records`` are mandatory: the first binds the unit of
    work to a trusted tenant, the second is how ``AcceptedPartitionEvidence`` reaches M06. Both
    have implementations now (``tenant_context.py``, ``evidence_ports.py``), so the registry no
    longer names them; an omitted one is reported as ``m05_required_port_missing``.
    """

    stage = MigrationStage.M05_INSTANCE_RUNTIME
    blockers: list[AssemblyBlocker] = []
    if upstream_evidence is None:
        blockers.append(_blocker(
            stage, M05_UPSTREAM_EVIDENCE_MISSING,
            "M05UpstreamEvidence (M04 scope gate + M04E export/marker + M05 runtime gate) is "
            "required; without it ready_for_import is False for every partition",
            port="M05UpstreamEvidence",
        ))
    if session_factory is None:
        blockers.append(_blocker(
            stage, M05_SESSION_FACTORY_MISSING,
            "an async_sessionmaker over PostgreSQL is required for the M05 unit of work",
            port="async_sessionmaker",
        ))
    blockers.extend(_required_port_blockers(
        stage, {"TrustedTenantContextPort": tenant_context,
                "M05PromotionRecordPort": promotion_records},
    ))
    context_usable = runtime_context is not None and bool(runtime_context.usable)
    if not context_usable:
        blockers.append(_blocker(
            stage, M05_RUNTIME_CONTEXT_MISSING,
            "a usable TrustedRuntimeContext (resolved tenant, subject, session, correlation) "
            "is required; the import store and reader are run-bound",
            port="TrustedRuntimeContext",
        ))
    page_size_valid = 1 <= page_size <= 1000
    if not page_size_valid:
        blockers.append(_blocker(
            stage, M05_PAGE_SIZE_INVALID,
            f"page_size {page_size!r} is outside the bounded 1..1000 contract",
            port="page_size",
        ))

    services: list[tuple[str, object]] = []
    if session_factory is not None and tenant_context is not None:
        uow = SqlAlchemyM05UnitOfWork(
            session_factory=session_factory, tenant_context=tenant_context,
        )
        identity = SqlAlchemyM05IdentityEvidence(uow, sessions=identity_sessions)
        audit = SqlAlchemyRedactedAuditSink(session_factory=session_factory)
        services.append(("m05_unit_of_work", uow))
        services.append(("identity_evidence", identity))
        services.append(("audit_sink", audit))
        if context_usable and page_size_valid and runtime_context is not None:
            reader = SqlAlchemyPinnedSnapshotReader(uow, runtime_context)
            store = SqlAlchemyInstanceImportStore(uow, identity, runtime_context)
            services.append(("candidate_reader", reader))
            services.append(("import_store", store))
            services.append(("import_service", InstanceImportService(
                reader, store, identity, audit,
                upstream=upstream_evidence, page_size=page_size,
            )))
            services.append(("resolve_plugin_instance_port", build_sql_resolve_plugin_instance_port(
                uow=uow, identity_evidence=identity, audit=audit, upstream=upstream_evidence,
            )))
        if promotion_records is not None:
            services.append(("promotion_service", M05PromotionService(promotion_records)))
    return StageAssembly(stage=stage, services=tuple(services), blockers=_dedupe(tuple(blockers)))


# ---------------------------------------------------------------------------
# M06 — shadow grants gateway and bounded parity
# ---------------------------------------------------------------------------

M06_SHADOW_GATEWAY_PORTS_MISSING = "m06_shadow_gateway_ports_missing"


def build_m06_grants_gateway_stage(
    *,
    ports: ShadowGatewayPorts | None,
    parity_observations: object | None,
    parity_summaries: ParityEvidenceSummaryPort | None,
    parity_reports: ParityReportStorePort | None,
) -> StageAssembly:
    """Wire M06: the evidence-only shadow facade plus the bounded parity runner.

    ``build_shadow_gateway`` refuses ``legacy_scope=None``/``legacy_quota=None`` (exact
    parity needs both owners live); that refusal is converted into a named blocker rather
    than propagated, because it is a composition fact, not a request failure.

    The facade stays shadow: ``DECISION_OWNER``/``CANONICAL_READ_OWNER`` remain ``legacy`` and
    canonical positive writes stay off. ``ports.m05_upstream`` must be the same
    ``M05UpstreamEvidence`` object M05 was assembled with, otherwise identity resolution and
    the handoff gates disagree.

    Blocker ``m06_partition_handoff_read_port_unimplemented`` (review §3 G3) is always
    present: ``DependencyGates`` therefore always resolves to ``None`` today, no shadow
    evidence is ever written, and ``ParityRunReport`` cannot accumulate real observations.
    """

    stage = MigrationStage.M06_GRANTS_GATEWAY
    blockers: list[AssemblyBlocker] = []
    services: list[tuple[str, object]] = []
    if ports is None:
        blockers.append(_blocker(
            stage, M06_SHADOW_GATEWAY_PORTS_MISSING,
            "ShadowGatewayPorts must be stated explicitly; there is no partial shadow facade",
            port="ShadowGatewayPorts",
        ))
    else:
        try:
            gateway: CapabilityGatewayService = build_shadow_gateway(ports)
        except ValueError as exc:
            blockers.append(_blocker(
                stage, M06_LEGACY_SCOPE_AND_QUOTA_REQUIRED,
                f"shadow facade refused to construct: {exc}",
                port="CapabilityScopePort/CapabilityQuotaPort",
            ))
        else:
            services.append(("shadow_gateway", gateway))
    blockers.extend(_port_blockers(stage, {"PartitionHandoffReadPort":
                                           ports.handoff_partitions if ports is not None else None}))

    if parity_observations is None:
        blockers.append(_blocker(
            stage, M06_PARITY_OBSERVATIONS_MISSING,
            "bounded parity needs an observation source; a runner without it reports nothing",
            port="Observations",
        ))
    if parity_summaries is None:
        blockers.append(_blocker(
            stage, M06_PARITY_SUMMARIES_MISSING,
            "parity evidence summaries must be read from accepted evidence, not recomputed",
            port="ParityEvidenceSummaryPort",
        ))
    if parity_reports is None:
        blockers.append(_blocker(
            stage, M06_PARITY_REPORT_STORE_MISSING,
            "recorded parity runs require a report store; without it GateEvidence has no "
            "report_digest to bind",
            port="ParityReportStorePort",
        ))
    if parity_observations is not None and parity_summaries is not None:
        runner: BoundedParityRunner = build_parity_runner(
            observations=parity_observations, summaries=parity_summaries,
            reports=parity_reports,
        )
        services.append(("parity_runner", runner))
    return StageAssembly(stage=stage, services=tuple(services), blockers=_dedupe(tuple(blockers)))


@dataclass(frozen=True, slots=True)
class M06DurableEvidencePorts:
    """The three durable M06 evidence adapters, built from one session factory.

    These are the only M06 collaborators that *do* have implementations in
    ``src/prodavan`` (``SqlCanonicalEvidenceStore``, ``SqlParityEvidenceSummaryReader``,
    ``SqlParityReportStore``); the review records them as orphans because nothing constructed
    them. They are offered here so the host does not hand-roll them — but each one still keeps
    its own record-level refusals, and the M06 stage stays blocked on
    ``PartitionHandoffReadPort`` regardless.

    Caveat carried over from the adapter docstrings: the append-only privilege review and the
    partition RLS review for these tables are still open. Building these objects is therefore
    necessary but not sufficient for a production M06 binding.
    """

    evidence_read: EvidenceReadPort
    evidence_transaction: TrustedEvidenceTransactionPort
    parity_observations: object
    parity_summaries: ParityEvidenceSummaryPort
    parity_reports: ParityReportStorePort


def build_m06_durable_evidence_ports(
    *, session_factory: "SessionFactory | None"
) -> tuple[M06DurableEvidencePorts | None, tuple[AssemblyBlocker, ...]]:
    """Build the durable M06 evidence adapters, or explain why they cannot be built."""

    if session_factory is None:
        return None, (_blocker(
            MigrationStage.M06_GRANTS_GATEWAY, M06_SESSION_FACTORY_MISSING,
            "durable M06 evidence reads/writes need an async_sessionmaker; there is no "
            "in-memory fallback on the production path",
            port="async_sessionmaker",
        ),)
    store = SqlCanonicalEvidenceStore(session_factory=session_factory)
    return (
        M06DurableEvidencePorts(
            evidence_read=store,
            evidence_transaction=store,
            parity_observations=store,
            parity_summaries=SqlParityEvidenceSummaryReader(session_factory=session_factory),
            parity_reports=SqlParityReportStore(session_factory=session_factory),
        ),
        (),
    )


# ---------------------------------------------------------------------------
# cutover — per-partition ownership transition
# ---------------------------------------------------------------------------


def build_cutover_stage(
    *,
    repository: CutoverRepository | None,
    gate_verifier: GateVerifier | None,
    legacy_owner_verifier: LegacyOwnerVerifier | None,
    rollback_verifier: RollbackVerifier | None,
    canonical_repair_verifier: CanonicalRepairVerifier | None,
    max_command_bytes: int = 16_384,
) -> StageAssembly:
    """Wire cutover. Every dependency is mandatory; all five have implementations now.

    ``CutoverCommandService`` refuses a transition with a missing verifier
    (``trusted_*_verifier_missing``); the composition reports the same facts statically so a
    host cannot believe cutover is reachable because the object graph was built. With all five
    dependencies supplied the stage **is** wired — the ports live in
    ``application/migration/m06_cutover/ports.py`` (``SqlCutoverRepository`` and the four SQL
    verifiers), so review §3 G5 is closed. Two facts survive that wiring:

    * ``SqlCutoverRepository`` refuses every operation until its two tables exist
      (``ports.CUTOVER_REQUIRED_SCHEMA``); a missing schema is reported when the repository is
      used, not by this builder, because the builder opens no connection.
    * nothing converts a ``ParityRunReport`` plus the M05 promotion into
      ``GateEvidence``/``GateLineageEvidence`` (review §5 I6): a host still has to supply that
      mapper, and the verifiers only *check* what it produced.

    An omitted dependency is reported as ``cutover_required_port_missing`` naming the port, so
    a partially wired graph never looks merely "not yet".
    """

    stage = MigrationStage.CUTOVER
    blockers = list(_required_port_blockers(stage, {
        "CutoverRepository": repository,
        "GateVerifier": gate_verifier,
        "LegacyOwnerVerifier": legacy_owner_verifier,
        "RollbackVerifier": rollback_verifier,
        "CanonicalRepairVerifier": canonical_repair_verifier,
    }))
    bounded = 1 <= max_command_bytes <= 65_536
    if not bounded:
        blockers.append(_blocker(
            stage, CUTOVER_MAX_COMMAND_BYTES_INVALID,
            f"max_command_bytes {max_command_bytes!r} is outside 1..65536",
            port="max_command_bytes",
        ))
    services: list[tuple[str, object]] = []
    if repository is not None and bounded:
        services.append(("cutover_command_service", CutoverCommandService(
            repository,
            gate_verifier=gate_verifier,
            legacy_owner_verifier=legacy_owner_verifier,
            rollback_verifier=rollback_verifier,
            repair_verifier=canonical_repair_verifier,
            max_command_bytes=max_command_bytes,
        )))
    return StageAssembly(stage=stage, services=tuple(services), blockers=_dedupe(tuple(blockers)))


# ---------------------------------------------------------------------------
# Sequence — ordered stages with their preconditions (pure data; runs nothing)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StagePrecondition:
    """What must already hold before a stage may run.

    ``requires_stages`` is the ordering constraint; ``requires_evidence`` names the accepted
    evidence objects that must exist for *this exact partition*; ``gate_names`` are the
    literal reviewer-visible gate identities the stage re-checks. Nothing here is evaluated
    by the composition root — a caller decides when a stage may run.
    """

    requires_stages: tuple[MigrationStage, ...]
    requires_evidence: tuple[str, ...]
    gate_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One step of the chain: stage -> class -> required ports -> precondition."""

    stage: MigrationStage
    service_class: str
    produces: tuple[str, ...]
    required_ports: tuple[str, ...]
    precondition: StagePrecondition


def _required_ports(stage: MigrationStage, extra: tuple[str, ...]) -> tuple[str, ...]:
    """Declared-but-unimplemented ports of ``stage`` first, then the existing ones."""

    merged = [port.port for port in unimplemented_ports(stage)]
    for name in extra:
        if name not in merged:
            merged.append(name)
    return tuple(merged)


MIGRATION_SEQUENCE: tuple[StageSpec, ...] = (
    StageSpec(
        stage=MigrationStage.M04_SCOPE,
        service_class="SqlAlchemyScopeBackfillAdapter",
        produces=("ScopeResolutionEvidence", "ScopeMigrationResult", "BackfillCheckpoint"),
        required_ports=_required_ports(
            MigrationStage.M04_SCOPE, ("async_sessionmaker", "TenantScopeContextPort"),
        ),
        precondition=StagePrecondition(
            requires_stages=(),
            requires_evidence=(
                "MigrationEvidenceContext (server-derived tenant, transaction and correlation)",
                "reviewed M04-01/02 owner-registry RLS manifest",
            ),
        ),
    ),
    StageSpec(
        stage=MigrationStage.M04E_DEFINITION_VERSION,
        service_class="DefinitionVersionEvidenceBackfill",
        produces=("BackfillBatch", "M05EvidenceExport", "M05HandoffMarker", "DefinitionResolution"),
        required_ports=_required_ports(
            MigrationStage.M04E_DEFINITION_VERSION,
            ("ArtifactContentPort", "ServerDefinitionVersionRepository"),
        ),
        precondition=StagePrecondition(
            requires_stages=(MigrationStage.M04_SCOPE,),
            requires_evidence=(
                "ScopeResolutionEvidence for the same partition and writer generation",
                "complete digest-bound version BackfillCheckpoint (phase='version', phase_complete)",
            ),
            gate_names=("MIG-M04-01", "MIG-M04-02"),
        ),
    ),
    StageSpec(
        stage=MigrationStage.M05_INSTANCE_RUNTIME,
        service_class="InstanceImportService",
        produces=("ImportBatch", "InstanceImportRecord", "PromotionRecord", "RuntimeSnapshot"),
        required_ports=_required_ports(
            MigrationStage.M05_INSTANCE_RUNTIME,
            (
                "async_sessionmaker",
                "TrustedTenantContextPort",
                "TrustedRuntimeContext",
                "M05IdentityEvidencePort",
                "RedactedRuntimeAuditPort",
                "RuntimeSessionOwnershipPort",
            ),
        ),
        precondition=StagePrecondition(
            requires_stages=(MigrationStage.M04E_DEFINITION_VERSION,),
            requires_evidence=(
                "M05HandoffMarker MIG-M04E-02 (durable, review §3 G1)",
                "M04ScopeGateEvidence and M05RuntimeGateEvidence bound to the same snapshot",
                "exact instance key plus subject/session binding evidence",
            ),
            gate_names=(
                "MIG-M04-01",
                "MIG-M04-02",
                "MIG-M04E-01",
                "active_typed_parent_scope",
                "same_definition_version",
                "immutable_artifact",
            ),
        ),
    ),
    StageSpec(
        stage=MigrationStage.M06_GRANTS_GATEWAY,
        service_class="CapabilityGatewayService",
        produces=("DependencyGates", "ShadowObservation", "ParityRunReport"),
        required_ports=_required_ports(
            MigrationStage.M06_GRANTS_GATEWAY,
            (
                "ShadowGatewayPorts",
                "ObservationReadPort",
                "ParityEvidenceSummaryPort",
                "ParityReportStorePort",
                "CapabilityScopePort",
                "CapabilityQuotaPort",
            ),
        ),
        precondition=StagePrecondition(
            requires_stages=(MigrationStage.M05_INSTANCE_RUNTIME,),
            requires_evidence=(
                "AcceptedPartitionEvidence for the exact tenant/definition/version/instance",
                "normalized writer_epoch (review §5 I3: writer_generation is not an epoch)",
                "canonically ordered, digest-consistent accepted source inventory (review §5 I7)",
                "M05UpstreamEvidence identical to the instance the M05 stage was assembled with",
            ),
        ),
    ),
    StageSpec(
        stage=MigrationStage.CUTOVER,
        service_class="CutoverCommandService",
        produces=("PartitionCutover", "PromotionEvidence", "RollbackRecord"),
        required_ports=_required_ports(
            MigrationStage.CUTOVER,
            (
                # Implemented in m06_cutover/ports.py (MIG-CUTOVER-PORTS-01), so they are no
                # longer registry gaps -- but the stage still cannot run without them.
                "CutoverRepository",
                "GateVerifier",
                "LegacyOwnerVerifier",
                "RollbackVerifier",
                "CanonicalRepairVerifier",
            ),
        ),
        precondition=StagePrecondition(
            requires_stages=(
                MigrationStage.M04_SCOPE,
                MigrationStage.M04E_DEFINITION_VERSION,
                MigrationStage.M05_INSTANCE_RUNTIME,
                MigrationStage.M06_GRANTS_GATEWAY,
            ),
            requires_evidence=(
                "GateLineageEvidence chain M04 -> M04E -> M05 linked by parent evidence id + digest",
                "GateEvidence built from the recorded ParityRunReport (mapper absent, review §5 I6)",
                "LegacyOwnerEvidence, LegacyWriterDrainEvidence and FinalDeltaEvidence for the partition",
            ),
            gate_names=("M04", "M04E", "M05"),
        ),
    ),
)


def validate_migration_sequence(
    specs: tuple[StageSpec, ...] = MIGRATION_SEQUENCE,
) -> tuple[StageSpec, ...]:
    """Structural checks on the ordering; returns ``specs`` so import-time use stays terse.

    Raises ``ValueError`` when a stage is missing, repeated, or placed before a stage it
    requires. Called at import time: a bad edit to :data:`MIGRATION_SEQUENCE` fails on import
    rather than at the first cutover attempt.
    """

    order = [spec.stage for spec in specs]
    if order != list(MigrationStage):
        raise ValueError(
            "migration sequence must contain every stage exactly once and in reviewed order: "
            f"expected {[stage.value for stage in MigrationStage]}, got "
            f"{[stage.value for stage in order]}"
        )
    position = {stage: index for index, stage in enumerate(order)}
    for index, spec in enumerate(specs):
        for required in spec.precondition.requires_stages:
            if position[required] >= index:
                raise ValueError(
                    f"{spec.stage.value} requires {required.value}, which is not earlier in "
                    "the sequence"
                )
        if not spec.required_ports:
            raise ValueError(f"{spec.stage.value} declares no required ports")
        if spec.stage not in STAGE_BUILDERS:
            raise ValueError(f"{spec.stage.value} has no assembly function")
    return specs


def migration_sequence() -> tuple[StageSpec, ...]:
    """The ordered M04 -> M04E -> M05 -> M06 -> cutover specs. Starts nothing."""

    return MIGRATION_SEQUENCE


def stage_spec(stage: MigrationStage) -> StageSpec:
    """The spec for ``stage``, for callers that only know which step they are on."""

    for spec in MIGRATION_SEQUENCE:
        if spec.stage is stage:
            return spec
    raise KeyError(stage)


# ---------------------------------------------------------------------------
# Whole-chain assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainDependencies:
    """Every dependency of the chain, stated explicitly — no field has a default.

    ``None`` is a legitimate value (the dependency does not exist yet); it always turns into
    a named blocker for its stage, so an omitted dependency can never be mistaken for a
    working one.
    """

    session_factory: "SessionFactory | None"
    m04_tenant_context: TenantScopeContextPort | None
    m04e_reader: CandidateReader | None
    m04e_store: EvidenceStore | None
    m04e_gates: UpstreamGatePort | None
    m04e_proofs: CandidateProofPort | None
    m04e_artifacts: ArtifactContentPort | None
    m04e_server_repository: ServerDefinitionVersionRepository | None
    m04e_m05_handoff: M05HandoffPort | None
    m04e_page_size: int
    m05_tenant_context: TrustedTenantContextPort | None
    m05_runtime_context: TrustedRuntimeContext | None
    m05_upstream_evidence: M05UpstreamEvidence | None
    m05_promotion_records: M05PromotionRecordPort | None
    m05_identity_sessions: RuntimeSessionOwnershipPort | None
    m05_page_size: int
    m06_ports: ShadowGatewayPorts | None
    m06_parity_observations: object | None
    m06_parity_summaries: ParityEvidenceSummaryPort | None
    m06_parity_reports: ParityReportStorePort | None
    cutover_repository: CutoverRepository | None
    cutover_gate_verifier: GateVerifier | None
    cutover_legacy_owner_verifier: LegacyOwnerVerifier | None
    cutover_rollback_verifier: RollbackVerifier | None
    cutover_canonical_repair_verifier: CanonicalRepairVerifier | None


STAGE_BUILDERS: Mapping[MigrationStage, Callable[..., StageAssembly]] = {
    MigrationStage.M04_SCOPE: build_m04_scope_stage,
    MigrationStage.M04E_DEFINITION_VERSION: build_m04e_definition_version_stage,
    MigrationStage.M05_INSTANCE_RUNTIME: build_m05_instance_runtime_stage,
    MigrationStage.M06_GRANTS_GATEWAY: build_m06_grants_gateway_stage,
    MigrationStage.CUTOVER: build_cutover_stage,
}
# Validate the ordering now that the builder table exists (see validate_migration_sequence).
validate_migration_sequence()


@dataclass(frozen=True, slots=True)
class MigrationChain:
    """All five stage assemblies, in reviewed order, with their blockers preserved."""

    stages: tuple[StageAssembly, ...]

    def __post_init__(self) -> None:
        expected = tuple(spec.stage for spec in MIGRATION_SEQUENCE)
        actual = tuple(stage.stage for stage in self.stages)
        if actual != expected:
            raise ValueError(
                "migration chain must cover every stage in reviewed order: "
                f"expected {[stage.value for stage in expected]}, got "
                f"{[stage.value for stage in actual]}"
            )

    def by_stage(self, stage: MigrationStage) -> StageAssembly:
        for assembly in self.stages:
            if assembly.stage is stage:
                return assembly
        raise KeyError(stage)

    @property
    def ordered_stages(self) -> tuple[MigrationStage, ...]:
        return tuple(assembly.stage for assembly in self.stages)

    @property
    def blockers(self) -> tuple[AssemblyBlocker, ...]:
        return tuple(blocker for assembly in self.stages for blocker in assembly.blockers)

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(blocker.code for blocker in self.blockers)

    @property
    def wired(self) -> bool:
        """True only when every stage is a complete production binding."""

        return all(assembly.wired for assembly in self.stages)

    @property
    def first_blocker(self) -> AssemblyBlocker | None:
        """The earliest blocker in chain order — the real reason cutover is unreachable."""

        return next(iter(self.blockers), None)


def assemble_migration_chain(dependencies: ChainDependencies) -> MigrationChain:
    """Assemble every stage from ``dependencies``; nothing is started, no transaction opens."""

    deps = dependencies
    stages = (
        build_m04_scope_stage(
            session_factory=deps.session_factory,
            tenant_context=deps.m04_tenant_context,
        ),
        build_m04e_definition_version_stage(
            reader=deps.m04e_reader,
            store=deps.m04e_store,
            gates=deps.m04e_gates,
            proofs=deps.m04e_proofs,
            artifacts=deps.m04e_artifacts,
            server_repository=deps.m04e_server_repository,
            m05_handoff=deps.m04e_m05_handoff,
            page_size=deps.m04e_page_size,
        ),
        build_m05_instance_runtime_stage(
            session_factory=deps.session_factory,
            tenant_context=deps.m05_tenant_context,
            runtime_context=deps.m05_runtime_context,
            upstream_evidence=deps.m05_upstream_evidence,
            promotion_records=deps.m05_promotion_records,
            identity_sessions=deps.m05_identity_sessions,
            page_size=deps.m05_page_size,
        ),
        build_m06_grants_gateway_stage(
            ports=deps.m06_ports,
            parity_observations=deps.m06_parity_observations,
            parity_summaries=deps.m06_parity_summaries,
            parity_reports=deps.m06_parity_reports,
        ),
        build_cutover_stage(
            repository=deps.cutover_repository,
            gate_verifier=deps.cutover_gate_verifier,
            legacy_owner_verifier=deps.cutover_legacy_owner_verifier,
            rollback_verifier=deps.cutover_rollback_verifier,
            canonical_repair_verifier=deps.cutover_canonical_repair_verifier,
        ),
    )
    return MigrationChain(stages=stages)


__all__ = [
    "AssemblyBlocker",
    "ChainDependencies",
    "M04_SESSION_FACTORY_MISSING",
    "M04E_ARTIFACT_CONTENT_PORT_MISSING",
    "M04E_PAGE_SIZE_INVALID",
    "M04E_SERVER_REPOSITORY_MISSING",
    "M05_PAGE_SIZE_INVALID",
    "M05_RUNTIME_CONTEXT_MISSING",
    "M05_SESSION_FACTORY_MISSING",
    "M05_UPSTREAM_EVIDENCE_MISSING",
    "M06_LEGACY_SCOPE_AND_QUOTA_REQUIRED",
    "M06_PARITY_OBSERVATIONS_MISSING",
    "M06_PARITY_REPORT_STORE_MISSING",
    "M06_PARITY_SUMMARIES_MISSING",
    "M06_SESSION_FACTORY_MISSING",
    "M06_SHADOW_GATEWAY_PORTS_MISSING",
    "M06DurableEvidencePorts",
    "MIGRATION_SEQUENCE",
    "MigrationChain",
    "MigrationStage",
    "STAGE_BUILDERS",
    "StageAssembly",
    "StagePrecondition",
    "StageSpec",
    "CUTOVER_MAX_COMMAND_BYTES_INVALID",
    "UNIMPLEMENTED_PORTS",
    "UnimplementedPort",
    "assemble_migration_chain",
    "build_cutover_stage",
    "build_m04_scope_stage",
    "build_m04e_definition_version_stage",
    "build_m05_instance_runtime_stage",
    "build_m06_durable_evidence_ports",
    "build_m06_grants_gateway_stage",
    "migration_sequence",
    "stage_spec",
    "unimplemented_ports",
    "validate_migration_sequence",
]
