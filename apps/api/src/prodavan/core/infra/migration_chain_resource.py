"""Lifespan wiring that makes the migration chain reachable from the process.

Task ``MIG-COMPOSITION-WIRING-01``.

``application/migration/composition.py`` can build the whole
M04 -> M04E -> M05 -> M06 -> cutover chain (:func:`build_m04_scope_stage` and
friends, :func:`assemble_migration_chain`, :data:`MIGRATION_SEQUENCE`,
:data:`UNIMPLEMENTED_PORTS`), but ``core/wiring.py`` never mentioned migration:
in a delivered process the chain could be neither assembled, nor run, nor
diagnosed. This module is that seam, and it is the only place in the real
process that composes it.

What the resource does — and, deliberately, does not
----------------------------------------------------
* flag ``MIGRATION_CHAIN_ENABLED`` (default **off**): with the flag off the
  resource does nothing at all — it resolves no factory, constructs no adapter,
  touches no ``app.state`` attribute — and answers ``health() is None``
  ("not applicable"), exactly like the other opt-in hosts.
* ``startup`` resolves the process-scoped collaborators
  (:func:`build_migration_chain_dependencies`) and calls
  ``assemble_migration_chain``; the resulting ``MigrationChain`` is published on
  ``app.state.migration_chain`` and read back with :func:`get_migration_chain`.
* **nothing is started.** No backfill page is read, no cutover transition is
  attempted, no transaction opens, no writer is enabled: assembly is pure
  construction, and every stage keeps the fail-closed defaults the reviewed
  builders give it (M06 stays ``DECISION_OWNER == "legacy"``, canonical positive
  writes stay off).
* ``shutdown`` drops the references again — a stopped process must not keep a
  graph whose session-bound stores belong to a disposed engine.

Why several dependencies are structurally absent at process scope
------------------------------------------------------------------
The chain is assembled from what a *process* can legitimately own. Everything
else stays ``None`` and turns into a named blocker, never into a substitute:

=============================  ==================================================
dependency                     why it cannot exist at startup
=============================  ==================================================
``m04_tenant_context``,
``m05_tenant_context``         ``RlsTenantScopeContextAdapter`` /
                               ``RlsTrustedTenantContextAdapter`` need a
                               ``TrustedPrincipalResolver``. That resolver now
                               exists (``application/migration/principal_resolver.py``),
                               but a *process* can only use the durable variant
                               (``build_durable_principal_resolver``), which reads
                               ``auth_sessions`` — so the two adapters are composed
                               only when ``AUTH_SESSION_STORE_ENABLED`` is on. With
                               the flag off no row can exist, a process-scope
                               resolver could only refuse, and the adapters stay
                               absent with the codes below.
``m04e_store``                 ``build_m04e_evidence_store`` answers ``None`` with
                               the open DDL gaps until that schema is reviewed.
``m04e_artifacts``,
``m04e_server_repository``     both adapters are bound to a *caller-owned*
                               ``AsyncSession`` inside one SERIALIZABLE
                               transaction; a session factory cannot satisfy them.
``m04e_m05_handoff``,
``m05_promotion_records``,
partition handoff reader       the evidence stores require a resolved
                               ``TrustedEvidenceScope`` — a per-tenant,
                               per-partition identity a process does not have.
``m05_runtime_context``,
``m05_upstream_evidence``      a run/partition-bound context and the exact
                               upstream gate evidence for one instance.
``m06_ports``                  ``ShadowGatewayPorts`` needs every M06 port at
                               once. The legacy repository and scope ports now
                               have SQL implementations
                               (``infrastructure/persistence/dynamic_app_gateway``)
                               and are composed here, but the legacy **quota**
                               port has no table that can express a
                               per-invocation limit, and the identity/handoff
                               collaborators are per-partition. So the port set
                               stays absent and the quota absence is named.
=============================  ==================================================

Those absences are exactly what ``assemble_migration_chain`` reports per stage,
so the published report never has to guess: the stage blocking codes come from
the composition root itself, and this module adds only the *why at process
scope* codes listed below.

Fail-closed, but never fatal
----------------------------
A composition error is **data**, not an outage. ``startup`` catches it, logs one
code (never a message — an exception message can embed a DSN) and leaves
``health() is False``. The API must still boot: a diagnostic resource that can
crash-loop the process would be worse than no resource at all.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from prodavan.core.lifespan.resource import LifespanResource

if TYPE_CHECKING:  # pragma: no cover - typing only, runtime imports stay lazy
    from prodavan.application.migration.composition import ChainDependencies, MigrationChain

logger = logging.getLogger(__name__)

#: Stable resource id (also the health-report key).
RESOURCE_NAME: Final[str] = "migration_chain"

#: ``app.state`` attribute the assembled chain is published on.
STATE_ATTR: Final[str] = "migration_chain"

#: Bounded page size handed to both backfill stages. The builders reject anything
#: outside ``1..1000``; a process-wide default must never be the reason a stage is
#: refused, so the value is stated once here.
M04E_PAGE_SIZE: Final[int] = 100
M05_PAGE_SIZE: Final[int] = 100

# ---------------------------------------------------------------------------
# Resource-level codes (always codes, never messages: a message can carry a DSN)
# ---------------------------------------------------------------------------

#: The process session factory could not be resolved; nothing can be composed.
BLOCKER_SESSION_FACTORY_UNAVAILABLE = "migration_chain_session_factory_unavailable"
#: ``assemble_migration_chain`` (or the dependency resolution itself) raised.
BLOCKER_COMPOSITION_FAILED = "migration_chain_composition_failed"

#: ``TrustedPrincipalResolver`` is an auth-boundary collaborator. It exists now, but the
#: process-scope variant needs the durable ``auth_sessions`` store: with
#: ``AUTH_SESSION_STORE_ENABLED`` off there is nothing a process could resolve, so these
#: codes still describe the absent adapters.
PART_M04_TENANT_CONTEXT_UNAVAILABLE = "migration_chain_m04_tenant_context_unavailable"
PART_M05_TENANT_CONTEXT_UNAVAILABLE = "migration_chain_m05_tenant_context_unavailable"
#: The durable resolver itself could not be constructed (a composition error, not a gap).
PART_DURABLE_PRINCIPAL_RESOLVER_FAILED = "migration_chain_durable_principal_resolver_failed"
#: Both M04E adapters are bound to a caller-owned transaction, not to a factory.
PART_M04E_ARTIFACTS_UNAVAILABLE = "migration_chain_artifact_content_session_bound"
PART_M04E_SERVER_REPOSITORY_UNAVAILABLE = (
    "migration_chain_server_definition_repository_session_bound"
)
#: Evidence stores need a resolved tenant/partition; a process has none.
PART_EVIDENCE_SCOPE_IS_PER_PARTITION = "migration_chain_evidence_scope_is_per_partition"
#: Run-bound M05 inputs: resolved tenant/subject/session and exact upstream gates.
PART_M05_RUNTIME_CONTEXT_UNAVAILABLE = "migration_chain_runtime_context_is_per_run"
PART_M05_UPSTREAM_EVIDENCE_UNAVAILABLE = "migration_chain_upstream_evidence_is_per_partition"
#: ``ShadowGatewayPorts`` cannot be stated without this port, and no table can decide it.
PART_M06_LEGACY_QUOTA_TABLE_MISSING = "migration_chain_legacy_quota_table_missing"
#: The remaining M06 ports (identity, handoff, mapping, canonical) are per-partition.
PART_M06_IDENTITY_AND_HANDOFF_PER_PARTITION = (
    "migration_chain_m06_identity_and_handoff_are_per_partition"
)
#: The shadow facade's port set is incomplete, so the stage must stay unwired.
PART_M06_SHADOW_PORTS_UNAVAILABLE = "migration_chain_shadow_gateway_ports_unavailable"

#: "The caller did not pass this factory" — distinct from an explicit ``None``,
#: which means "this process deliberately has no session factory".
_UNSET: Final[Any] = object()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _dedupe(codes: Iterable[str]) -> tuple[str, ...]:
    """Preserve order, drop blanks and repeats."""

    seen: set[str] = set()
    ordered: list[str] = []
    for code in codes:
        if code and code not in seen:
            seen.add(code)
            ordered.append(code)
    return tuple(ordered)


def _code_of(blocker: str) -> str:
    """A builder blocker is ``"code: detail"``; only the code leaves this module."""

    return blocker.split(":", 1)[0].strip()


def _safe_reason(exc: BaseException) -> str:
    """Exception *class* as a bounded code; a message can carry a connection string."""

    name = type(exc).__name__
    cleaned = "".join(character.lower() if character.isalnum() else "_" for character in name)
    return cleaned.strip("_") or "composition_error"


def _attempt(
    part: str,
    thunk: Callable[[], Any],
    *,
    built: list[str],
    blockers: list[str],
) -> Any | None:
    """Run one constructor; a refusal becomes a code, never an exception."""

    try:
        value = thunk()
    except Exception as exc:  # noqa: BLE001 - a composition failure is data here
        blockers.append(f"migration_chain_{part}_failed")
        logger.warning(
            "migration chain: %s composition failed error=%s code=migration_chain_%s_failed",
            part,
            _safe_reason(exc),
            part,
        )
        return None
    if value is not None:
        built.append(part)
    return value


def _pair(
    part: str,
    thunk: Callable[[], tuple[Any | None, tuple[str, ...]]],
    *,
    built: list[str],
    blockers: list[str],
) -> Any | None:
    """Run one ``(object | None, codes)`` builder from this tree.

    Those builders already answer with redacted ``"code: detail"`` strings in
    exactly the shape the composition root uses; the codes are folded in as-is
    (after stripping the detail) and the object is returned for the chain.
    """

    try:
        value, codes = thunk()
    except Exception as exc:  # noqa: BLE001 - a composition failure is data here
        blockers.append(f"migration_chain_{part}_failed")
        logger.warning(
            "migration chain: %s composition failed error=%s code=migration_chain_%s_failed",
            part,
            _safe_reason(exc),
            part,
        )
        return None
    blockers.extend(_code_of(code) for code in codes if code)
    if value is not None:
        built.append(part)
    return value


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainDependencySet:
    """``ChainDependencies`` plus what this process could and could not compose.

    ``built`` names the collaborators that really were constructed (so a report
    can show work done, not just work missing); ``blockers`` are codes only, in
    the order they were discovered. Neither list is a substitute for the stage
    blockers the composition root derives from ``dependencies`` itself.
    """

    dependencies: ChainDependencies
    built: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


def _empty_dependencies(session_factory: Any) -> ChainDependencies:
    """Every dependency absent — the honest shape of a process with no database."""

    from prodavan.application.migration.composition import ChainDependencies

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


def build_migration_chain_dependencies(
    *, session_factory: Any = _UNSET, durable_principals: bool | None = None
) -> ChainDependencySet:
    """Resolve every dependency a *process* can own, and name the ones it cannot.

    ``session_factory``:
        omitted -> ``get_session_factory()`` is resolved (an unusable factory is
        the named ``migration_chain_session_factory_unavailable`` blocker);
        ``None`` -> this process deliberately has no database, so no port is
        composed and only stage blockers are reported;
        a factory -> used verbatim (tests and hosts with a partial composition).

    ``durable_principals``:
        ``None`` -> follow ``settings.auth_session_store_enabled``. ``True`` -> compose the
        durable principal resolver (``build_durable_principal_resolver``, which reads
        ``auth_sessions``) and the M04/M05 tenant-context adapters over it. ``False`` ->
        leave both absent, exactly as before the store existed.

    The request path is untouched by all of this: a request resolves through
    ``BearerJwtCredentialSource`` with the token it carries. The durable source exists for
    callers that hold no token (a worker, a queued job, a second replica), which is why it
    is wired here — at process scope — and never into ``api/deps.py``.

    Never raises. Nothing here opens a connection, starts a transaction or calls
    a stage method: every constructor in this tree only stores ports and
    validates scalars.
    """

    from prodavan.application.migration.composition import (
        build_m06_durable_evidence_ports,
    )
    from prodavan.application.migration.m04e_ports import (
        build_m04e_evidence_store,
        build_m04e_sql_ports,
    )
    from prodavan.application.migration.m06_cutover.ports import build_cutover_ports
    from prodavan.infrastructure.persistence.dynamic_app_gateway import (
        build_legacy_gateway_ports,
    )

    built: list[str] = []
    blockers: list[str] = []

    factory = session_factory
    if factory is _UNSET:
        try:
            from prodavan.infrastructure.persistence.database import get_session_factory

            factory = get_session_factory()
        except Exception as exc:  # noqa: BLE001 - an unusable factory is a blocker
            factory = None
            blockers.append(BLOCKER_SESSION_FACTORY_UNAVAILABLE)
            logger.warning(
                "migration chain: session factory unavailable error=%s code=%s",
                _safe_reason(exc),
                BLOCKER_SESSION_FACTORY_UNAVAILABLE,
            )

    if factory is None:
        # Nothing below can exist without a factory; the composition root still
        # names every missing port per stage, so no extra code is invented here.
        return ChainDependencySet(
            dependencies=_empty_dependencies(None),
            built=(),
            blockers=_dedupe(blockers),
        )

    if durable_principals is None:
        # The store and the writer are one decision: ``api/deps.py::get_principal`` writes
        # an ``auth_sessions`` row only when this flag is on, so a process-scope resolver is
        # only composed when such a row can exist.
        from prodavan.config.settings import settings

        durable_principals = bool(getattr(settings, "auth_session_store_enabled", False))

    m04e_ports = _pair(
        "m04e_sql_ports",
        lambda: build_m04e_sql_ports(
            session_factory=factory, page_size_limit=M04E_PAGE_SIZE
        ),
        built=built,
        blockers=blockers,
    )
    if m04e_ports is not None:
        # ``M04E_PORT_WIRING_BLOCKERS`` is addressed to *the composition root*
        # ("keep until DDL-backed stores are reviewed"), unlike the cutover schema
        # blockers, which the repository itself reports per call. So they are part
        # of this resource's diagnostics; they are not a stage blocker, because the
        # composition root owns stage wiring, not review status.
        blockers.extend(_code_of(code) for code in m04e_ports.blockers)
    # ``build_m04e_evidence_store`` answers ``(None, schema gaps)`` today: the
    # M04E checkpoint/mapping tables are still an open DDL review. The codes are
    # folded in above, so the stage blocker and its reason stay visible together.
    store = _pair(
        "m04e_evidence_store",
        lambda: build_m04e_evidence_store(session_factory=factory),
        built=built,
        blockers=blockers,
    )
    m06_evidence = _pair(
        "m06_durable_evidence_ports",
        lambda: build_m06_durable_evidence_ports(session_factory=factory),
        built=built,
        blockers=blockers,
    )
    cutover = _attempt(
        "cutover_ports",
        lambda: build_cutover_ports(session_factory=factory),
        built=built,
        blockers=blockers,
    )

    principal_resolver = None
    if durable_principals:
        from prodavan.application.migration.principal_resolver import (
            build_durable_principal_resolver,
        )

        principal_resolver = _attempt(
            "durable_principal_resolver",
            lambda: build_durable_principal_resolver(session_factory=factory),
            built=built,
            blockers=blockers,
        )

    m04_tenant_context = None
    m05_tenant_context = None
    if principal_resolver is not None:
        from prodavan.application.migration.tenant_context import (
            RlsTenantScopeContextAdapter,
            RlsTrustedTenantContextAdapter,
        )

        m04_tenant_context = RlsTenantScopeContextAdapter(
            principal_resolver=principal_resolver
        )
        m05_tenant_context = RlsTrustedTenantContextAdapter(
            principal_resolver=principal_resolver
        )

    # The legacy gateway ports: two of the three ``build_shadow_gateway`` needs are
    # implemented over the ``dynamic_app_*`` tables and constructed here. They are
    # *composed*, not published: without the quota port ``ShadowGatewayPorts``
    # cannot be stated, and inventing the port set around the hole would turn
    # "quota enforced" into "quota ignored" for every shadow observation.
    legacy_ports = _attempt(
        "legacy_gateway_ports",
        lambda: build_legacy_gateway_ports(session_factory=factory),
        built=built,
        blockers=blockers,
    )
    if legacy_ports is not None:
        # ``_attempt`` records only exceptions; the partial-port codes are data, so
        # they are merged explicitly (``_dedupe`` at the end makes a repeat harmless).
        blockers.extend(legacy_ports.blockers)
        built.append("legacy_gateway_repository")
        built.append("legacy_gateway_scope")

    # Every absence below is structural (see the module docstring table), not a
    # missing implementation this task could have supplied.
    structural_absences = [
        PART_M04E_ARTIFACTS_UNAVAILABLE,
        PART_M04E_SERVER_REPOSITORY_UNAVAILABLE,
        PART_EVIDENCE_SCOPE_IS_PER_PARTITION,
        PART_M05_RUNTIME_CONTEXT_UNAVAILABLE,
        PART_M05_UPSTREAM_EVIDENCE_UNAVAILABLE,
        PART_M06_LEGACY_QUOTA_TABLE_MISSING,
        PART_M06_IDENTITY_AND_HANDOFF_PER_PARTITION,
        PART_M06_SHADOW_PORTS_UNAVAILABLE,
    ]
    if m04_tenant_context is None:
        structural_absences.insert(0, PART_M04_TENANT_CONTEXT_UNAVAILABLE)
    if m05_tenant_context is None:
        structural_absences.append(PART_M05_TENANT_CONTEXT_UNAVAILABLE)
    blockers.extend(structural_absences)

    from prodavan.application.migration.composition import ChainDependencies

    dependencies = ChainDependencies(
        session_factory=factory,
        m04_tenant_context=m04_tenant_context,
        m04e_reader=m04e_ports.reader if m04e_ports is not None else None,
        m04e_store=store,
        m04e_gates=m04e_ports.gates if m04e_ports is not None else None,
        m04e_proofs=m04e_ports.proofs if m04e_ports is not None else None,
        # Caller-owned SERIALIZABLE transaction adapters; a factory cannot build them.
        m04e_artifacts=None,
        m04e_server_repository=None,
        # Evidence stores are tenant/partition bound (``TrustedEvidenceScope``).
        m04e_m05_handoff=None,
        m04e_page_size=M04E_PAGE_SIZE,
        m05_tenant_context=m05_tenant_context,
        m05_runtime_context=None,
        m05_upstream_evidence=None,
        m05_promotion_records=None,
        m05_identity_sessions=None,
        m05_page_size=M05_PAGE_SIZE,
        m06_ports=None,
        m06_parity_observations=(
            m06_evidence.parity_observations if m06_evidence is not None else None
        ),
        m06_parity_summaries=(
            m06_evidence.parity_summaries if m06_evidence is not None else None
        ),
        m06_parity_reports=(
            m06_evidence.parity_reports if m06_evidence is not None else None
        ),
        cutover_repository=cutover.repository if cutover is not None else None,
        cutover_gate_verifier=cutover.gate_verifier if cutover is not None else None,
        cutover_legacy_owner_verifier=(
            cutover.legacy_owner_verifier if cutover is not None else None
        ),
        cutover_rollback_verifier=(
            cutover.rollback_verifier if cutover is not None else None
        ),
        cutover_canonical_repair_verifier=(
            cutover.canonical_repair_verifier if cutover is not None else None
        ),
    )
    return ChainDependencySet(
        dependencies=dependencies,
        built=tuple(built),
        blockers=_dedupe(blockers),
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageReport:
    """One stage: what was constructed, whether it is wired, which codes block it."""

    stage: str
    wired: bool
    services: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MigrationChainReport:
    """Diagnostics safe for logs and probes: stage names, service names, codes.

    No object is carried here that could print a DSN or a payload; every entry
    is a name or a redacted code from a closed vocabulary.
    """

    enabled: bool
    assembled: bool
    wired: bool
    stages: tuple[StageReport, ...] = ()
    composition_blockers: tuple[str, ...] = ()
    startup_blocker: str | None = None
    built: tuple[str, ...] = ()

    @property
    def blockers(self) -> tuple[str, ...]:
        """Every reason the chain is not fully wired: resource codes first, then stages."""

        return _dedupe(
            (*self.composition_blockers, *(code for stage in self.stages for code in stage.blockers))
        )

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe copy (no callables, no objects, no messages)."""

        return {
            "enabled": self.enabled,
            "assembled": self.assembled,
            "wired": self.wired,
            "startup_blocker": self.startup_blocker,
            "built": list(self.built),
            "composition_blockers": list(self.composition_blockers),
            "blockers": list(self.blockers),
            "stages": [
                {
                    "stage": stage.stage,
                    "wired": stage.wired,
                    "services": list(stage.services),
                    "blockers": list(stage.blockers),
                }
                for stage in self.stages
            ],
        }


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


def migration_chain_resource_from_settings() -> MigrationChainResource:
    """Resource built from process settings (registered in ``core/wiring``)."""

    from prodavan.config.settings import settings

    return MigrationChainResource(enabled=bool(getattr(settings, "migration_chain_enabled", False)))


def get_migration_chain(app: Any) -> MigrationChain | None:
    """The chain published by a started resource, or ``None``.

    Mirrors ``get_web_stream_runtime``: the published object is removed (not set
    to ``None``) on shutdown, so a probe cannot mistake a torn-down graph for a
    live one.
    """

    state = getattr(app, "state", None)
    if state is None:  # pragma: no cover - defensive
        return None
    return getattr(state, STATE_ATTR, None)


class MigrationChainResource(LifespanResource):
    """Assemble the migration chain once and publish its diagnostics.

    Two seams exist for tests and for hosts with a partial composition:
    ``dependency_builder`` says *what* to assemble (default: every collaborator
    this process can own) and ``chain_builder`` says *how* (default:
    ``assemble_migration_chain``). Neither may be used to soften a blocker: the
    default chain builder is the reviewed composition root, and a chain is only
    ever reported wired when all five stages are wired.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        dependency_builder: Callable[[], ChainDependencySet] | None = None,
        chain_builder: Callable[[ChainDependencies], MigrationChain] | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._dependency_builder = dependency_builder
        self._chain_builder = chain_builder
        self._app: Any = None
        self._chain: MigrationChain | None = None
        self._dependency_set: ChainDependencySet | None = None
        self._composition_blockers: tuple[str, ...] = ()
        self._startup_blocker: str | None = None
        self._published = False

    # -- LifespanResource --------------------------------------------------

    @property
    def name(self) -> str:
        return RESOURCE_NAME

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def chain(self) -> MigrationChain | None:
        """The assembled chain, or ``None``; cleared on shutdown."""

        return self._chain

    @property
    def dependencies(self) -> ChainDependencies | None:
        """The dependency set the chain was assembled from; cleared on shutdown."""

        return None if self._dependency_set is None else self._dependency_set.dependencies

    @property
    def composition_blockers(self) -> tuple[str, ...]:
        """Process-scope codes explaining which collaborators could not be composed."""

        return self._composition_blockers

    @property
    def built(self) -> tuple[str, ...]:
        """Collaborators that really were constructed while resolving dependencies."""

        return () if self._dependency_set is None else self._dependency_set.built

    @property
    def blockers(self) -> tuple[str, ...]:
        """Every blocking code for this resource, resource-first then stages."""

        return self.report().blockers

    @property
    def startup_blocker(self) -> str | None:
        """Why the chain was not assembled at all, when it was not."""

        return self._startup_blocker

    def bind_app(self, app: Any) -> None:
        """Receive the ASGI app from ``LifespanManager`` (it forwards it lazily)."""

        if app is self._app:
            return
        if self._published:
            # A different app object must never inherit this chain's state.
            self._withdraw()
        self._app = app

    async def startup(self) -> None:
        if not self._enabled:
            # Never leave a chain published while the flag is off: with the flag
            # off the process must behave exactly as it did before this resource
            # existed, which includes resolving no session factory at all.
            self._reset()
            logger.info(
                "migration chain: disabled (MIGRATION_CHAIN_ENABLED=false); nothing assembled"
            )
            return

        self._reset()
        try:
            dependency_set = (
                self._dependency_builder()
                if self._dependency_builder is not None
                else build_migration_chain_dependencies()
            )
            chain = (
                self._chain_builder(dependency_set.dependencies)
                if self._chain_builder is not None
                else _assemble_chain(dependency_set.dependencies)
            )
        except Exception as exc:  # noqa: BLE001 - startup must not crash-loop the API
            self._startup_blocker = BLOCKER_COMPOSITION_FAILED
            self._composition_blockers = (BLOCKER_COMPOSITION_FAILED,)
            self._chain = None
            self._dependency_set = None
            logger.error(
                "migration chain: composition failed error=%s; the process starts with "
                "health=false (%s)",
                _safe_reason(exc),
                BLOCKER_COMPOSITION_FAILED,
            )
            return

        self._dependency_set = dependency_set
        self._composition_blockers = tuple(dependency_set.blockers)
        self._chain = chain
        self._publish(chain)

        if chain.wired and not self._composition_blockers:
            logger.info(
                "migration chain: assembled and fully wired stages=%s",
                ",".join(assembly.stage.value for assembly in chain.stages),
            )
        else:
            logger.warning(
                "migration chain: assembled with blockers=%s built=%s",
                ",".join(_dedupe((*self._composition_blockers, *chain.blocker_codes))),
                ",".join(self.built) or "-",
            )

    async def shutdown(self) -> None:
        self._withdraw()
        self._reset()

    async def health(self) -> bool | None:
        if not self._enabled:
            return None
        if self._startup_blocker is not None or self._chain is None:
            return False
        return bool(self._chain.wired and not self._composition_blockers)

    # -- report ------------------------------------------------------------

    def report(self) -> MigrationChainReport:
        """Stage-by-stage diagnostics; safe to log and to serialise."""

        chain = self._chain
        if chain is None:
            return MigrationChainReport(
                enabled=self._enabled,
                assembled=False,
                wired=False,
                composition_blockers=self._composition_blockers,
                startup_blocker=self._startup_blocker,
            )
        stages = tuple(
            StageReport(
                stage=assembly.stage.value,
                wired=assembly.wired,
                services=tuple(symbol for symbol, _instance in assembly.services),
                blockers=assembly.blocker_codes,
            )
            for assembly in chain.stages
        )
        return MigrationChainReport(
            enabled=self._enabled,
            assembled=True,
            wired=bool(chain.wired and not self._composition_blockers),
            stages=stages,
            composition_blockers=self._composition_blockers,
            startup_blocker=self._startup_blocker,
            built=self.built,
        )

    # -- internals ---------------------------------------------------------

    def _reset(self) -> None:
        self._chain = None
        self._dependency_set = None
        self._composition_blockers = ()
        self._startup_blocker = None

    def _publish(self, chain: MigrationChain) -> None:
        state = getattr(self._app, "state", None)
        if state is None:
            # A resource started by a bare ``LifespanManager()`` without an app is
            # still a valid composition; only publishing needs the handle.
            self._published = False
            return
        setattr(state, STATE_ATTR, chain)
        self._published = True

    def _withdraw(self) -> None:
        """Remove the published chain, if this resource published it."""

        app, chain, published = self._app, self._chain, self._published
        self._published = False
        if not published or app is None or chain is None:
            return
        state = getattr(app, "state", None)
        if state is None:  # pragma: no cover - defensive
            return
        if getattr(state, STATE_ATTR, None) is not chain:
            # Somebody else owns that state now; never delete their chain.
            return
        try:
            delattr(state, STATE_ATTR)
        except AttributeError:  # pragma: no cover - concurrent withdraw
            logger.debug("migration chain: state already cleared")


def _assemble_chain(dependencies: ChainDependencies) -> MigrationChain:
    """Import the reviewed composition root lazily and assemble.

    Imported here rather than at module import so ``core`` does not pull the
    whole migration application layer into every process entry point — and so a
    test that replaces the builder never touches it.
    """

    from prodavan.application.migration.composition import assemble_migration_chain

    return assemble_migration_chain(dependencies)


__all__ = [
    "BLOCKER_COMPOSITION_FAILED",
    "BLOCKER_SESSION_FACTORY_UNAVAILABLE",
    "M04E_PAGE_SIZE",
    "M05_PAGE_SIZE",
    "PART_EVIDENCE_SCOPE_IS_PER_PARTITION",
    "PART_DURABLE_PRINCIPAL_RESOLVER_FAILED",
    "PART_M04E_ARTIFACTS_UNAVAILABLE",
    "PART_M04E_SERVER_REPOSITORY_UNAVAILABLE",
    "PART_M04_TENANT_CONTEXT_UNAVAILABLE",
    "PART_M05_RUNTIME_CONTEXT_UNAVAILABLE",
    "PART_M05_TENANT_CONTEXT_UNAVAILABLE",
    "PART_M05_UPSTREAM_EVIDENCE_UNAVAILABLE",
    "PART_M06_IDENTITY_AND_HANDOFF_PER_PARTITION",
    "PART_M06_LEGACY_QUOTA_TABLE_MISSING",
    "PART_M06_SHADOW_PORTS_UNAVAILABLE",
    "RESOURCE_NAME",
    "STATE_ATTR",
    "ChainDependencySet",
    "MigrationChainReport",
    "MigrationChainResource",
    "StageReport",
    "build_migration_chain_dependencies",
    "get_migration_chain",
    "migration_chain_resource_from_settings",
]
