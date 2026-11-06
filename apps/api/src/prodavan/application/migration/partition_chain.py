"""Per-partition assembly of the M04 -> M04E -> M05 -> M06 -> cutover chain.

Task ``MIG-PARTITION-ASSEMBLY-01``.

Why a second assembly root exists
=================================

``application/migration/composition.py`` assembles the chain **once per process**
(``core/infra/migration_chain_resource.py``).  That is deliberately incomplete, and the
reason is a type: two evidence stores cannot be owned by a process at all.

.. code-block:: text

    SqlM05HandoffEvidenceStore(*, session_factory, scope: TrustedEvidenceScope)
    SqlM05PromotionEvidenceStore(*, session_factory, scope: TrustedEvidenceScope)

``TrustedEvidenceScope`` is ``(tenant_id, writer_generation)`` -- i.e. **a partition**.  A
process has no single tenant and no single writer generation, so the process-scope builder
must leave ``m04e_m05_handoff`` and ``m05_promotion_records`` as ``None`` forever.  That is
blocker ``migration_chain_evidence_scope_is_per_partition`` in the lifespan resource: it is
a *correct* report, not a wiring omission.

This module is the missing assembly root: it takes the one fact a process cannot have -- the
partition key -- and builds the **whole** ``ChainDependencies`` for that single partition,
with every evidence port constructed against ``TrustedEvidenceScope(tenant, writer_generation)``.

What is derivable from a partition, and what is not
===================================================

Derivable from ``(key, session_factory, writer_generation)`` alone:

======================================  =============================================
dependency                               constructed as
======================================  =============================================
``m04e_reader``/``gates``/``proofs``      ``build_m04e_sql_ports(session_factory=...)``
``m04e_artifacts``                        ``build_m04e_artifact_content_store(factory)``
``m04e_store``                            ``SqlM04EBackfillEvidenceStore(factory, scope)``
``m04e_m05_handoff``                      ``SqlM05HandoffEvidenceStore(factory, scope)``
``m05_promotion_records``                 ``SqlM05PromotionEvidenceStore(factory, scope)``
``m06_parity_observations``/             ``build_m06_durable_evidence_ports(factory)``
``summaries``/``reports``
``cutover_repository`` + 4 verifiers      ``build_cutover_ports(factory)``
======================================  =============================================

Derivable **only** when the operator supplies one collaborator:

* ``m04_tenant_context`` / ``m05_tenant_context`` -- from ``principal_resolver``.  The two
  RLS adapters refuse to write a GUC unless an auth-boundary lookup turns a correlation id
  into a sealed principal (``tenant_context.py``), so the resolver is stated, never guessed.

``m05_tenant_context`` / ``m05_runtime_context`` / ``m05_identity_sessions`` /
``m05_upstream_evidence`` are additionally derivable through :mod:`m05_contexts`, the
reviewed per-partition builder for exactly those four fields:

* ``build_m05_tenant_context`` wraps the RLS binder over the same ``principal_resolver`` this
  planner already takes for M04;
* ``build_m05_runtime_context`` validates the instance owner's **server-resolved**
  tenant/subject/session/correlation -- it cannot invent them, because the import proves
  ownership against those exact values (``_resolve_instance_binding`` compares
  ``identity.subject_id``/``session_id`` with the context and calls
  ``prove_subject_session``);
* ``build_m05_identity_sessions`` supplies the ``RuntimeSessionOwnershipPort`` the tree had
  no implementation of (``SqlAuthSessionOwnership`` over ``auth_sessions``);
* ``build_m05_upstream_evidence`` assembles ``M04ScopeGateEvidence`` +
  ``M05RuntimeGateEvidence`` from this partition's stored M04-02 row, a reviewed
  runtime-gate manifest and the measured instance counts, and takes the M04E
  ``M05EvidenceExport`` either as an input or read out of the partition's ledger by
  ``m04e_export_reader.SqlM04EEvidenceExportReader`` (through
  ``m05_contexts.load_partition_m05_contexts``).  The accepted rows are real ledger rows --
  the marker's counts are still not evidence, but the rows they count now exist in
  ``m04e_definition_version_{mappings,quarantine,checkpoints,audit}``.

:class:`PartitionAssemblyOverrides` therefore accepts the whole bundle as one
:class:`~prodavan.application.migration.m05_contexts.M05ContextInputs` value
(``m05_contexts=``), so an operator states the partition's run inputs once instead of
re-deriving four objects.  An individual override still wins when given, and an omitted
input still produces exactly one named blocker.

Genuinely **not** derivable, even for one partition -- each one is a named blocker below, and
each names exactly what would make the partition executable.  None of them is stubbed:

* ``m04e_server_repository`` -- ``ServerDefinitionVersionRepository`` wraps a resolver bound
  to a **caller-owned** ``AsyncSession`` inside one SERIALIZABLE transaction; a factory
  cannot produce the session the caller must own.
* ``m06_ports`` -- ``ShadowGatewayPorts`` needs the legacy capability owners
  (``legacy_scope``/``legacy_quota``), ``company``, ``canonical_grants`` and
  ``method_mappings``; no composition root publishes those adapters, and
  ``build_shadow_gateway`` refuses without the two legacy owners because exact parity needs
  both live.

Fail-closed rules honoured here
===============================

* **No defaults that hide absence.**  Every dependency is either constructed or named in
  :attr:`PartitionChainPlan.blockers`; nothing is silently ``None``.
* **No exception for an incomplete set.**  A missing collaborator is data
  (:class:`PartitionChainBlocker`), because the operator entry point must be able to
  *report* what a partition still needs instead of crashing on it.
* **Two partitions never share state.**  Every evidence port is constructed with its own
  ``TrustedEvidenceScope``; a store built for partition A refuses a row of partition B
  before any SQL (``TrustedEvidenceScope.admits_partition``), and the planner never caches a
  scope across keys.
* **Nothing runs.**  Building a plan constructs objects and validates scalars.  No session
  is opened, no backfill page is read, no cutover transition is attempted; execution is
  ``partition_runner.py``'s job and it is one explicit call per partition per stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from prodavan.application.migration.composition import (
    ChainDependencies,
    MigrationChain,
    assemble_migration_chain,
    build_m06_durable_evidence_ports,
)
from prodavan.application.migration.evidence_ports import (
    ReviewedM04RlsEvidence,
    SqlM04EBackfillEvidenceStore,
    SqlM05HandoffEvidenceStore,
    SqlM05PromotionEvidenceStore,
    SqlPartitionHandoffReadStore,
    TrustedEvidenceScope,
)
from prodavan.application.migration.m04e_ports import build_m04e_sql_ports
from prodavan.application.migration.m05_contexts import (
    M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING,
    M05ContextBlocker,
    M05ContextInputs,
    build_m04_scope_gate,
    build_m05_identity_sessions,
    build_m05_runtime_context,
    build_m05_tenant_context,
    build_m05_upstream_evidence,
)
from prodavan.application.migration.m06_cutover.ports import build_cutover_ports
from prodavan.application.migration.tenant_context import (
    RlsTenantScopeContextAdapter,
    TrustedPrincipalResolver,
)
from prodavan.infrastructure.persistence.migration_artifact_store import (
    build_m04e_artifact_content_store,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from prodavan.application.migration.m04e_definition_version.artifact import (
        ArtifactContentPort,
    )
    from prodavan.application.migration.m04e_definition_version.repository import (
        ServerDefinitionVersionRepository,
    )
    from prodavan.application.migration.m05_instance_runtime.instance_persistence import (
        RuntimeSessionOwnershipPort,
    )
    from prodavan.application.migration.m05_instance_runtime.runtime_service import (
        M05UpstreamEvidence,
        TrustedRuntimeContext,
    )
    from prodavan.application.migration.m06_grants_gateway.composition import (
        ShadowGatewayPorts,
    )

    SessionFactory = async_sessionmaker[AsyncSession]

# ---------------------------------------------------------------------------
# Blocker vocabulary (stable codes: an operator greps for these, not for prose)
# ---------------------------------------------------------------------------

PARTITION_KEY_INVALID = "partition_key_invalid"
PARTITION_SESSION_FACTORY_MISSING = "partition_session_factory_missing"
PARTITION_WRITER_GENERATION_INVALID = "partition_writer_generation_invalid"

#: ``TrustedPrincipalResolver`` is an auth-boundary collaborator: it turns a verified
#: credential into a sealed principal, so both RLS tenant-context adapters stay unbuildable
#: without it.  One blocker, two dependents (M04 and M05).
PARTITION_PRINCIPAL_RESOLVER_UNAVAILABLE = "partition_principal_resolver_unavailable"

PARTITION_M04E_PORTS_UNAVAILABLE = "partition_m04e_sql_ports_unavailable"
PARTITION_M04E_ARTIFACTS_UNAVAILABLE = "partition_artifact_content_port_unavailable"
PARTITION_M04E_SERVER_REPOSITORY_SESSION_BOUND = (
    "partition_server_definition_repository_session_bound"
)

PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE = "partition_runtime_context_requires_owner_session"
PARTITION_M05_UPSTREAM_EVIDENCE_UNAVAILABLE = "partition_upstream_evidence_unavailable"
PARTITION_M05_IDENTITY_SESSIONS_UNAVAILABLE = "partition_session_ownership_port_unavailable"

PARTITION_M06_EVIDENCE_PORTS_UNAVAILABLE = "partition_m06_evidence_ports_unavailable"
PARTITION_M06_SHADOW_PORTS_UNAVAILABLE = "partition_shadow_gateway_ports_unavailable"

PARTITION_CUTOVER_PORTS_UNAVAILABLE = "partition_cutover_ports_unavailable"

#: Bounded page sizes are the same scalars the composition root states; a partition planner
#: must not invent a different one, so the values live in one place.
M04E_PAGE_SIZE = 100
M05_PAGE_SIZE = 100

#: The artifact read bound every M04E caller already states (the composition root and
#: ``m04e_definition_version/repository.py`` both use 64 MiB), so the partition planner
#: hands the durable port the same ceiling instead of inventing its own.
M04E_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# Key
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartitionChainKey:
    """The two identifiers that *are* the migration partition.

    ``(tenant_id, app_definition_id)`` is exactly the pair every M04E/M05 evidence contract
    calls ``partition`` (``M05EvidenceExport``, ``BackfillCheckpoint.partition``,
    ``PromotionRecord.partition``, ``m05_partition_id``).  A key is immutable and orderable
    by value, so two calls with the same key really do address the same partition and two
    different keys can never collapse.
    """

    tenant_id: str
    app_definition_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("app_definition_id", self.app_definition_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"partition key requires a non-empty {name}")
            if value != value.strip():
                raise ValueError(f"partition key {name} must not carry padding whitespace")

    @property
    def partition(self) -> tuple[str, str]:
        """The plain tuple the evidence contracts use."""

        return (self.tenant_id, self.app_definition_id)

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.tenant_id}/{self.app_definition_id}"


def partition_key(value: object) -> PartitionChainKey:
    """Coerce a ``(tenant, definition)`` pair into a key; ``ValueError`` when it cannot be.

    Accepts a :class:`PartitionChainKey` or a two-element tuple of non-empty strings, so an
    operator CLI can pass what it already has without constructing the type by hand.
    """

    if isinstance(value, PartitionChainKey):
        return value
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return PartitionChainKey(tenant_id=str(value[0]), app_definition_id=str(value[1]))
    raise ValueError("a partition key is PartitionChainKey or a (tenant, definition) pair")


# ---------------------------------------------------------------------------
# Blocker
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartitionChainBlocker:
    """One concrete reason *this partition* is not fully executable.

    ``dependency`` is the ``ChainDependencies`` field (or collaborator) at fault and
    ``needed`` states what would remove the blocker.  Both are plain strings so a report can
    be logged and diffed without carrying an object graph or a DSN.
    """

    code: str
    dependency: str
    detail: str
    needed: str

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.code} ({self.dependency}): {self.detail}; needed: {self.needed}"


def _blocker(
    code: str, dependency: str, detail: str, needed: str
) -> PartitionChainBlocker:
    return PartitionChainBlocker(
        code=code, dependency=dependency, detail=detail, needed=needed
    )


def _code_of(text: str) -> str:
    """A dependency builder answers ``"code: detail"``; keep the code, drop the prose."""

    return text.split(":", 1)[0].strip()


def _valid_generation(value: object) -> bool:
    """A partition writer generation is a positive integer (``bool`` is not an integer)."""

    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 1
    )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartitionAssemblyOverrides:
    """The collaborators a partition cannot derive, stated once and carried as a value.

    Every field defaults to ``None`` -- which is *absence*, and absence becomes a named
    blocker -- with one exception: ``m04e_artifacts`` is **derivable** since
    ``MIG-ARTIFACT-PORT-01``, so leaving it ``None`` lets the planner construct the durable
    ``ArtifactContentPort`` for this partition.  An explicit value still wins, which is how
    a host that already holds a reviewed store keeps it.  The type exists so an operator
    entry point can accept one typed argument instead of ten keyword parameters, and so a
    report can show exactly what the host supplied (``PartitionChainPlan.built`` is the
    complement).
    """

    principal_resolver: TrustedPrincipalResolver | None = None
    m04e_artifacts: "ArtifactContentPort | None" = None
    m04e_server_repository: "ServerDefinitionVersionRepository | None" = None
    m04e_page_size: int = M04E_PAGE_SIZE
    m04e_max_artifact_bytes: int = M04E_MAX_ARTIFACT_BYTES
    m05_contexts: M05ContextInputs | None = None
    m05_runtime_context: "TrustedRuntimeContext | None" = None
    m05_upstream_evidence: "M05UpstreamEvidence | None" = None
    m05_identity_sessions: "RuntimeSessionOwnershipPort | None" = None
    m05_page_size: int = M05_PAGE_SIZE
    m06_shadow_ports: "ShadowGatewayPorts | None" = None
    reviewed_m04_rls: ReviewedM04RlsEvidence | None = None

    def chain_kwargs(self) -> dict[str, object]:
        """Keyword arguments for :func:`build_partition_chain`."""

        return {
            "principal_resolver": self.principal_resolver,
            "m04e_artifacts": self.m04e_artifacts,
            "m04e_server_repository": self.m04e_server_repository,
            "m04e_page_size": self.m04e_page_size,
            "m04e_max_artifact_bytes": self.m04e_max_artifact_bytes,
            "m05_contexts": self.m05_contexts,
            "m05_runtime_context": self.m05_runtime_context,
            "m05_upstream_evidence": self.m05_upstream_evidence,
            "m05_identity_sessions": self.m05_identity_sessions,
            "m05_page_size": self.m05_page_size,
            "m06_shadow_ports": self.m06_shadow_ports,
            "reviewed_m04_rls": self.reviewed_m04_rls,
        }


@dataclass(frozen=True, slots=True)
class PartitionChainPlan:
    """Everything one partition can be assembled from, plus everything it still needs.

    ``dependencies`` is a complete :class:`ChainDependencies` value -- every field is
    stated, and a field the partition cannot own is ``None`` *and* named in ``blockers``.
    ``built`` records what was really constructed, so a report can show work done rather
    than only work missing.

    ``partition_handoff_read`` is exposed because it is the one M06 collaborator that *is*
    per-partition (``SqlPartitionHandoffReadStore`` reads the accepted evidence of exactly
    this partition); a host that supplies ``ShadowGatewayPorts`` wires this instance, not a
    hand-rolled copy.
    """

    key: PartitionChainKey
    writer_generation: int
    evidence_scope: TrustedEvidenceScope | None
    dependencies: ChainDependencies
    blockers: tuple[PartitionChainBlocker, ...] = ()
    built: tuple[str, ...] = ()
    partition_handoff_read: SqlPartitionHandoffReadStore | None = None

    def __iter__(self) -> Iterator[object]:
        """``dependencies, blockers = build_partition_chain(...)``.

        The plan is a value, not a tuple; iteration is offered so callers that only want
        the two halves do not have to know about the extra fields (``key``, ``built``).
        """

        yield self.dependencies
        yield self.blockers

    @property
    def complete(self) -> bool:
        """True only when nothing this partition needs is still missing."""

        return not self.blockers

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(blocker.code for blocker in self.blockers)

    @property
    def missing(self) -> tuple[str, ...]:
        """The dependencies still unresolved, in discovery order."""

        return tuple(blocker.dependency for blocker in self.blockers)

    def blocker_for(self, code: str) -> PartitionChainBlocker | None:
        for blocker in self.blockers:
            if blocker.code == code:
                return blocker
        return None

    def snapshot(self) -> dict[str, object]:
        """JSON-safe diagnostics: names and codes only, never an object or a DSN."""

        return {
            "tenant_id": self.key.tenant_id,
            "app_definition_id": self.key.app_definition_id,
            "writer_generation": self.writer_generation,
            "complete": self.complete,
            "built": list(self.built),
            "blockers": [
                {
                    "code": blocker.code,
                    "dependency": blocker.dependency,
                    "detail": blocker.detail,
                    "needed": blocker.needed,
                }
                for blocker in self.blockers
            ],
        }


def _empty_dependencies(session_factory: "SessionFactory | None") -> ChainDependencies:
    """Every dependency absent -- the honest shape of a plan that could build nothing."""

    return ChainDependencies(
        session_factory=session_factory,
        m04_tenant_context=None,
        m04e_reader=None,
        m04e_store=None,
        m04e_gates=None,
        m04e_proofs=None,
        m04e_artifacts=None,
        m04e_server_repository=None,
        m04e_m05_handoff=None,
        m04e_page_size=M04E_PAGE_SIZE,
        m05_tenant_context=None,
        m05_runtime_context=None,
        m05_upstream_evidence=None,
        m05_promotion_records=None,
        m05_identity_sessions=None,
        m05_page_size=M05_PAGE_SIZE,
        m06_ports=None,
        m06_parity_observations=None,
        m06_parity_summaries=None,
        m06_parity_reports=None,
        cutover_repository=None,
        cutover_gate_verifier=None,
        cutover_legacy_owner_verifier=None,
        cutover_rollback_verifier=None,
        cutover_canonical_repair_verifier=None,
    )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_partition_chain(
    *,
    key: PartitionChainKey,
    session_factory: "SessionFactory | None",
    writer_generation: int,
    principal_resolver: TrustedPrincipalResolver | None = None,
    m04e_artifacts: "ArtifactContentPort | None" = None,
    m04e_server_repository: "ServerDefinitionVersionRepository | None" = None,
    m04e_page_size: int = M04E_PAGE_SIZE,
    m04e_max_artifact_bytes: int = M04E_MAX_ARTIFACT_BYTES,
    m05_contexts: M05ContextInputs | None = None,
    m05_runtime_context: "TrustedRuntimeContext | None" = None,
    m05_upstream_evidence: "M05UpstreamEvidence | None" = None,
    m05_identity_sessions: "RuntimeSessionOwnershipPort | None" = None,
    m05_page_size: int = M05_PAGE_SIZE,
    m06_shadow_ports: "ShadowGatewayPorts | None" = None,
    reviewed_m04_rls: ReviewedM04RlsEvidence | None = None,
) -> PartitionChainPlan:
    """Assemble the complete dependency set of **one** partition.

    Returns ``(dependencies, blockers)`` (a :class:`PartitionChainPlan`, which iterates as
    that pair).  An incomplete set is reported, never raised: the caller decides whether an
    incomplete plan is fatal, and a dry run reports it as a plan.

    ``principal_resolver`` builds **both** tenant-context adapters
    (``RlsTenantScopeContextAdapter`` / ``RlsTrustedTenantContextAdapter``) -- they are the
    reviewed binders, and the resolver is the only collaborator a partition cannot own.

    ``reviewed_m04_rls`` is not required for assembly, but
    ``SqlPartitionHandoffReadStore.read_partition`` refuses every partition without it
    (``m04_rls_review_missing``): the M04 RLS digest is recomputed from the live catalog
    against a security-reviewed manifest, and no approved manifest exists in the repository
    yet.  Stating it here keeps that refusal visible instead of hiding it behind the M06
    stage's own blockers.

    ``m04e_artifacts`` is an optional *override*: leaving it out constructs the durable
    ``ArtifactContentPort`` over this partition's session factory
    (``infrastructure/persistence/migration_artifact_store.py``), which is what removed
    blocker ``partition_artifact_content_port_unavailable``.  Construction opens no session
    and starts no object-store resource.

    ``m05_contexts`` is the one-argument form of the four M05 run contexts
    (:class:`~prodavan.application.migration.m05_contexts.M05ContextInputs`).  With it the
    planner derives ``m05_runtime_context``, ``m05_upstream_evidence`` and
    ``m05_identity_sessions`` from the partition's stated run inputs and fills
    ``m05_tenant_context`` through the same reviewed builder, so the M05 stage becomes
    *wired* for that partition instead of carrying three permanent blockers.  Each individual
    ``m05_*`` override still wins when given, and an omitted input still produces the named
    blocker of the builder that needed it -- so a partial bundle degrades into specific
    report lines, never into a substitute object.

    Every other keyword is an *override* for a dependency the partition cannot derive.  Each
    one left out produces exactly one named blocker; nothing is defaulted to a substitute.
    """

    blockers: list[PartitionChainBlocker] = []
    built: list[str] = []

    if not isinstance(key, PartitionChainKey):
        raise ValueError("build_partition_chain requires a PartitionChainKey")

    if session_factory is None:
        blockers.append(_blocker(
            PARTITION_SESSION_FACTORY_MISSING,
            "session_factory",
            "no async_sessionmaker was supplied, so no durable port of this partition can "
            "exist (there is no in-memory path anywhere in the chain)",
            "an async_sessionmaker over the platform PostgreSQL database",
        ))
        return PartitionChainPlan(
            key=key,
            writer_generation=writer_generation,
            evidence_scope=None,
            dependencies=_empty_dependencies(None),
            blockers=tuple(blockers),
            built=(),
        )

    scope: TrustedEvidenceScope | None = None
    if _valid_generation(writer_generation):
        scope = TrustedEvidenceScope(
            tenant_id=key.tenant_id, writer_generation=writer_generation
        )
    else:
        blockers.append(_blocker(
            PARTITION_WRITER_GENERATION_INVALID,
            "writer_generation",
            f"writer_generation {writer_generation!r} is not a positive integer, so no "
            "TrustedEvidenceScope can be built and the two partition-bound evidence stores "
            "stay unconstructible",
            "the partition's live writer generation (>= 1): generation 0 cannot be fenced by "
            "M06 (m05_writer_generation_zero_has_no_m06_epoch)",
        ))

    # -- M04 / M05 tenant contexts -----------------------------------------
    m04_tenant_context = None
    m05_tenant_context = None
    if principal_resolver is None:
        blockers.append(_blocker(
            PARTITION_PRINCIPAL_RESOLVER_UNAVAILABLE,
            "principal_resolver",
            "both RLS binders (m04_tenant_context and m05_tenant_context) refuse to write a "
            "GUC unless an auth-boundary lookup turns a correlation id into a sealed "
            "TrustedPrincipal; without it M04 and M05 have no trusted tenant context",
            "a TrustedPrincipalResolver for this operator run: either a verified-credential "
            "source for the request, or the durable auth_sessions relation "
            "(principal_resolver.AUTH_SESSION_CORRELATION_GAP) so a correlation id can be "
            "resolved outside the request that carried the token",
        ))
    else:
        m04_tenant_context = RlsTenantScopeContextAdapter(principal_resolver=principal_resolver)
        built.append("m04_tenant_context")

    # The M05 binder goes through the reviewed per-partition builder, which is the one place
    # that decides whether a binder may exist at all.  A host that isolates M05 (its own
    # resolver for the instance owner's run) states it in ``m05_contexts``; otherwise both
    # halves share this plan's resolver, which is what a single operator run wants.
    m05_resolver = (
        m05_contexts.principal_resolver
        if m05_contexts is not None and m05_contexts.principal_resolver is not None
        else principal_resolver
    )
    # An M05 binder is only built when the plan already has a resolver for M04 as well:
    # one plan addresses one operator run, and a half-authorized graph would let M05 write a
    # tenant GUC that M04 cannot.
    m05_tenant_build = (
        build_m05_tenant_context(principal_resolver=m05_resolver)
        if principal_resolver is not None
        else None
    )
    if m05_tenant_build is not None:
        if m05_tenant_build.value is not None:
            m05_tenant_context = m05_tenant_build.value
            built.append("m05_tenant_context")
        else:
            # ``principal_resolver`` is not None, so its only refusal is "not resolvable";
            # fold the builder's own codes in rather than inventing a different one.
            blockers.extend(
                _blocker(
                    PARTITION_PRINCIPAL_RESOLVER_UNAVAILABLE,
                    "m05_tenant_context",
                    blocker.detail,
                    blocker.needed,
                )
                for blocker in m05_tenant_build.blockers
            )

    # -- M04E reader / gates / proofs --------------------------------------
    m04e_ports, m04e_codes = build_m04e_sql_ports(
        session_factory=session_factory, page_size_limit=m04e_page_size
    )
    if m04e_ports is None:
        blockers.append(_blocker(
            PARTITION_M04E_PORTS_UNAVAILABLE,
            "m04e_reader/m04e_gates/m04e_proofs",
            "; ".join(_code_of(code) for code in m04e_codes) or "no M04E SQL ports",
            "an async_sessionmaker plus a bounded page size in 1..1000",
        ))
    else:
        built.extend(("m04e_reader", "m04e_gates", "m04e_proofs"))

    # -- partition-bound evidence stores (the reason this module exists) ----
    m04e_store = None
    m04e_m05_handoff = None
    m05_promotion_records = None
    partition_handoff_read = None
    if scope is not None:
        m04e_store = SqlM04EBackfillEvidenceStore(
            session_factory=session_factory, scope=scope
        )
        m04e_m05_handoff = SqlM05HandoffEvidenceStore(
            session_factory=session_factory, scope=scope
        )
        m05_promotion_records = SqlM05PromotionEvidenceStore(
            session_factory=session_factory, scope=scope
        )
        partition_handoff_read = SqlPartitionHandoffReadStore(
            session_factory=session_factory,
            scope=scope,
            reviewed_m04_rls=reviewed_m04_rls,
        )
        built.extend((
            "m04e_store",
            "m04e_m05_handoff",
            "m05_promotion_records",
            "partition_handoff_read",
        ))

    # -- M04E artifact content port ----------------------------------------
    # ``ArtifactContentPort`` is derivable, so it is no longer a blocker: an explicit
    # override wins (a host may hold a reviewed store), otherwise the durable adapter is
    # constructed over this partition's session factory.  Construction opens no session
    # and starts no FileStore resource -- ``SqlArtifactContentStore`` resolves the object
    # store lazily on the first read -- so "nothing runs" still holds.
    if m04e_artifacts is None:
        m04e_artifacts = build_m04e_artifact_content_store(
            session_factory=session_factory,
            max_object_bytes=m04e_max_artifact_bytes,
        )
    if m04e_artifacts is None:
        # Unreachable while ``session_factory`` is not None, but stated rather than
        # assumed: an absent port must stay a named blocker, never a silent ``None``.
        blockers.append(_blocker(
            PARTITION_M04E_ARTIFACTS_UNAVAILABLE,
            "m04e_artifacts",
            "the durable ArtifactContentPort could not be constructed for this partition",
            "an async_sessionmaker, or an explicit ArtifactContentPort override",
        ))
    else:
        built.append("m04e_artifacts")

    if m04e_server_repository is None:
        blockers.append(_blocker(
            PARTITION_M04E_SERVER_REPOSITORY_SESSION_BOUND,
            "m04e_server_repository",
            "ServerDefinitionVersionRepository wraps a resolver bound to a caller-owned "
            "AsyncSession inside one SERIALIZABLE transaction; a session factory cannot "
            "produce that session, and the composition must not open one on its behalf",
            "a ServerDefinitionVersionRepository built by the operator inside its own "
            "SERIALIZABLE transaction (resolver + ActiveTypedScopePort + "
            "DefinitionVersionEvidencePort + ArtifactContentPort)",
        ))
    else:
        built.append("m04e_server_repository")

    # -- M05 run-bound collaborators ---------------------------------------
    #
    # Three of the four M05 fields are *derivable* from the operator's stated run inputs
    # (:mod:`m05_contexts`); an explicit override wins when the host already holds the
    # object.  Either way the field is only filled with something that validates: a
    # ``TrustedRuntimeContext`` that is not ``usable`` is not a context, so it stays a
    # blocker rather than being stored.
    m05_runtime_blockers: tuple[M05ContextBlocker, ...] = ()
    if m05_contexts is not None:
        runtime_build = build_m05_runtime_context(
            tenant_id=m05_contexts.tenant_id,
            subject_id=m05_contexts.subject_id,
            session_id=m05_contexts.session_id,
            correlation_id=m05_contexts.correlation_id,
            authenticated=m05_contexts.authenticated,
        )
        if runtime_build.value is not None and (
            m05_runtime_context is None or not getattr(m05_runtime_context, "usable", False)
        ):
            m05_runtime_context = runtime_build.value
        if runtime_build.value is not None and (
            str(runtime_build.value.resolved_tenant_id) != key.tenant_id
        ):
            # The context is real but describes another tenant; storing it would let the
            # import import somebody else's instances.
            m05_runtime_blockers = (
                _blocker(
                    PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE,
                    "m05_runtime_context",
                    f"the stated runtime context resolves tenant "
                    f"{runtime_build.value.resolved_tenant_id!r}, but this plan is for "
                    f"{key.tenant_id!r}",
                    "the instance owner's runtime context for exactly this partition",
                ),
            )
            m05_runtime_context = None
    if m05_runtime_context is None or not getattr(m05_runtime_context, "usable", False):
        for blocker in m05_runtime_blockers or (
            _blocker(
                PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE,
                "m05_runtime_context",
                "the M05 import proves that the authenticated subject and session own the "
                "exact instance being imported; an operator run has no such context and "
                "inventing one would let the migration attribute an instance to the wrong "
                "owner",
                "a TrustedRuntimeContext carrying the instance owner's resolved tenant, "
                "subject and active session (plus correlation), obtained from the owner's "
                "authenticated session -- stated through m05_contexts= for this partition",
            ),
        ):
            blockers.append(blocker if isinstance(blocker, PartitionChainBlocker) else _blocker(
                blocker.code, blocker.dependency, blocker.detail, blocker.needed
            ))
    else:
        built.append("m05_runtime_context")

    m05_upstream_blockers: tuple[M05ContextBlocker, ...] = ()
    if m05_contexts is not None:
        upstream_build = build_m05_upstream_evidence(
            tenant_id=key.tenant_id,
            app_definition_id=key.app_definition_id,
            m04_scope=build_m04_scope_gate(
                tenant_id=key.tenant_id,
                app_definition_id=key.app_definition_id,
                row=m05_contexts.m04_scope_row,
            ).value,
            m04e_export=m05_contexts.m04e_export,
            m05_runtime=m05_contexts.m05_runtime,
            review=m05_contexts.review,
            counts=m05_contexts.instance_counts,
        )
        if upstream_build.value is not None and m05_upstream_evidence is None:
            m05_upstream_evidence = upstream_build.value
        m05_upstream_blockers = upstream_build.blockers
        if m05_contexts.m04e_export is None and m05_contexts.m04e_export_reader is not None:
            # This planner is synchronous, so it cannot perform the ledger read itself.  When
            # the host bound a reader but no export, the useful report is not "the export is
            # missing" but "read it": naming the async entry point is what makes the reader
            # discoverable instead of looking like an unused field.
            m05_upstream_blockers = tuple(
                _blocker(
                    blocker.code,
                    blocker.dependency,
                    f"{blocker.detail}; note: a reader is bound, so "
                    "m05_contexts.load_partition_m05_contexts(key=..., inputs=...) can read "
                    "this export instead of the host stating it",
                    blocker.needed,
                )
                if blocker.code == M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING
                else blocker
                for blocker in m05_upstream_blockers
            )
    if m05_upstream_evidence is None:
        for blocker in m05_upstream_blockers or (
            _blocker(
                PARTITION_M05_UPSTREAM_EVIDENCE_UNAVAILABLE,
                "m05_upstream_evidence",
                "M05UpstreamEvidence is the exact M04 scope gate, M04E export/handoff marker "
                "and M05 runtime gate of one snapshot; without it ready_for_import is False "
                "for every partition, and fabricating the digests would fabricate gate "
                "evidence",
                "the three gates of this partition's snapshot, stated through m05_contexts= "
                "(stored M04-02 row, M04E export with its marker, reviewed runtime-gate "
                "manifest and the measured instance counts)",
            ),
        ):
            blockers.append(blocker if isinstance(blocker, PartitionChainBlocker) else _blocker(
                blocker.code, blocker.dependency, blocker.detail, blocker.needed
            ))
    else:
        built.append("m05_upstream_evidence")

    if m05_contexts is not None:
        sessions_build = build_m05_identity_sessions(session_factory=session_factory)
        if sessions_build.value is not None and m05_identity_sessions is None:
            m05_identity_sessions = sessions_build.value
    if m05_identity_sessions is None:
        blockers.append(_blocker(
            PARTITION_M05_IDENTITY_SESSIONS_UNAVAILABLE,
            "m05_identity_sessions",
            "M05IdentityEvidence calls prove_subject_session before any row can be imported; "
            "without a RuntimeSessionOwnershipPort every candidate would be quarantined as "
            "unproven (subject_session_evidence_port_missing)",
            "the RuntimeSessionOwnershipPort built by "
            "m05_contexts.build_m05_identity_sessions (SqlAuthSessionOwnership over "
            "auth_sessions), which only needs this plan's session factory",
        ))
    else:
        built.append("m05_identity_sessions")

    # -- M06 ---------------------------------------------------------------
    if m06_shadow_ports is None:
        blockers.append(_blocker(
            PARTITION_M06_SHADOW_PORTS_UNAVAILABLE,
            "m06_ports",
            "ShadowGatewayPorts needs the live legacy owners (legacy_scope/legacy_quota), the "
            "company, canonical-grant and method-mapping adapters; build_shadow_gateway "
            "refuses without both legacy owners because exact parity compares them",
            "a ShadowGatewayPorts whose legacy_scope/legacy_quota are the live capability "
            "owners, with handoff_partitions set to this plan's partition_handoff_read and "
            "m05_upstream set to the same M05UpstreamEvidence M05 was assembled with — or a "
            "caller that composes the set from this partition through "
            "m06_grants_gateway.ports_assembly.build_shadow_ports(..., partition=key, "
            "writer_generation=writer_generation) and passes the complete plan here",
        ))
    else:
        built.append("m06_ports")

    m06_evidence, m06_codes = build_m06_durable_evidence_ports(
        session_factory=session_factory
    )
    if m06_evidence is None:
        blockers.append(_blocker(
            PARTITION_M06_EVIDENCE_PORTS_UNAVAILABLE,
            "m06_parity_observations/m06_parity_summaries/m06_parity_reports",
            "; ".join(_code_of(code) for code in m06_codes) or "no durable M06 evidence ports",
            "SqlCanonicalEvidenceStore + SqlParityEvidenceSummaryReader + "
            "SqlParityReportStore over the platform database",
        ))

    # -- cutover -----------------------------------------------------------
    cutover = build_cutover_ports(session_factory=session_factory)
    built.append("cutover_ports")

    dependencies = ChainDependencies(
        session_factory=session_factory,
        m04_tenant_context=m04_tenant_context,
        m04e_reader=m04e_ports.reader if m04e_ports is not None else None,
        m04e_store=m04e_store,
        m04e_gates=m04e_ports.gates if m04e_ports is not None else None,
        m04e_proofs=m04e_ports.proofs if m04e_ports is not None else None,
        m04e_artifacts=m04e_artifacts,
        m04e_server_repository=m04e_server_repository,
        m04e_m05_handoff=m04e_m05_handoff,
        m04e_page_size=m04e_page_size,
        m05_tenant_context=m05_tenant_context,
        m05_runtime_context=m05_runtime_context,
        m05_upstream_evidence=m05_upstream_evidence,
        m05_promotion_records=m05_promotion_records,
        m05_identity_sessions=m05_identity_sessions,
        m05_page_size=m05_page_size,
        m06_ports=m06_shadow_ports,
        m06_parity_observations=(
            m06_evidence.parity_observations if m06_evidence is not None else None
        ),
        m06_parity_summaries=(
            m06_evidence.parity_summaries if m06_evidence is not None else None
        ),
        m06_parity_reports=(
            m06_evidence.parity_reports if m06_evidence is not None else None
        ),
        cutover_repository=cutover.repository,
        cutover_gate_verifier=cutover.gate_verifier,
        cutover_legacy_owner_verifier=cutover.legacy_owner_verifier,
        cutover_rollback_verifier=cutover.rollback_verifier,
        cutover_canonical_repair_verifier=cutover.canonical_repair_verifier,
    )
    return PartitionChainPlan(
        key=key,
        writer_generation=writer_generation,
        evidence_scope=scope,
        dependencies=dependencies,
        blockers=tuple(blockers),
        built=tuple(built),
        partition_handoff_read=partition_handoff_read,
    )


def assemble_partition_chain(**kwargs: object) -> MigrationChain:
    """Build the partition's dependency set and hand it to the reviewed chain assembler.

    Exactly ``build_partition_chain(**kwargs)`` followed by
    ``assemble_migration_chain(dependencies)``: this module adds no stage logic of its own,
    so every stage keeps the blockers and fail-closed defaults the composition root gives it.
    The returned chain still reports its own stage blockers (:attr:`MigrationChain.blockers`);
    the partition-scope reasons live in the plan, which a caller keeps for diagnostics.
    """

    plan = build_partition_chain(**kwargs)  # type: ignore[arg-type]
    return assemble_migration_chain(plan.dependencies)


__all__ = [
    "M04E_MAX_ARTIFACT_BYTES",
    "M04E_PAGE_SIZE",
    "M05_PAGE_SIZE",
    "PARTITION_CUTOVER_PORTS_UNAVAILABLE",
    "PARTITION_KEY_INVALID",
    "PARTITION_M04E_ARTIFACTS_UNAVAILABLE",
    "PARTITION_M04E_PORTS_UNAVAILABLE",
    "PARTITION_M04E_SERVER_REPOSITORY_SESSION_BOUND",
    "PARTITION_M05_IDENTITY_SESSIONS_UNAVAILABLE",
    "PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE",
    "PARTITION_M05_UPSTREAM_EVIDENCE_UNAVAILABLE",
    "PARTITION_M06_EVIDENCE_PORTS_UNAVAILABLE",
    "PARTITION_M06_SHADOW_PORTS_UNAVAILABLE",
    "PARTITION_PRINCIPAL_RESOLVER_UNAVAILABLE",
    "PARTITION_SESSION_FACTORY_MISSING",
    "PARTITION_WRITER_GENERATION_INVALID",
    "PartitionAssemblyOverrides",
    "M05ContextInputs",
    "PartitionChainBlocker",
    "PartitionChainKey",
    "PartitionChainPlan",
    "assemble_partition_chain",
    "build_partition_chain",
    "partition_key",
]
