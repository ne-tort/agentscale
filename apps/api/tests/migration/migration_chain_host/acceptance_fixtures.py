"""Test doubles for the migration chain host (``MIG-COMPOSITION-WIRING-01``).

Two rules shape everything here.

**1. The session factory refuses to be called.** The single most important
property of the resource is that assembly *starts nothing*: no backfill page, no
cutover transition, no transaction. A factory that counts its calls and raises
when used proves it — if any composed port opened a session during startup, the
test would see ``opened > 0`` or a composition code, not a green run.

**2. Only real construction where real construction is possible.** The dependency
resolver and the composition root under test are the production ones; the doubles
below only replace what a *process* genuinely cannot own (a tenant principal, a
run-bound runtime context). The one fabricated value is
:func:`wired_chain`, which exists to pin the health *predicate* — a chain in
which every stage reports ``wired`` — because today's composition root cannot
produce one (the registered ``PartitionHandoffReadPort`` gap keeps M06 unwired).
That is asserted separately, so the fabricated chain never stands in for the real
answer.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from prodavan.application.migration.composition import (
    MIGRATION_SEQUENCE,
    ChainDependencies,
    MigrationChain,
    MigrationStage,
    StageAssembly,
)
from prodavan.core.infra.migration_chain_resource import (
    STATE_ATTR,
    ChainDependencySet,
    MigrationChainResource,
    build_migration_chain_dependencies,
)
from prodavan.core.lifespan.manager import LifespanManager


class RefusingSessionFactory:
    """A callable factory that records and refuses every attempt to open a session.

    Callable on purpose: the ports accept ``async_sessionmaker`` and only ever
    *call* it inside a port method, so a plain callable is the exact seam that
    separates "assembled" from "ran".
    """

    def __init__(self) -> None:
        self.opened = 0

    def __call__(self) -> Any:
        self.opened += 1
        raise AssertionError(
            "assembly must not open a database session: the chain resource only composes"
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"RefusingSessionFactory(opened={self.opened})"


# ---------------------------------------------------------------------------
# Dependency sets
# ---------------------------------------------------------------------------


def full_dependencies(
    factory: RefusingSessionFactory | None = None,
) -> ChainDependencySet:
    """Every collaborator a process can own, resolved from the real tree."""

    return build_migration_chain_dependencies(
        session_factory=factory if factory is not None else RefusingSessionFactory()
    )


def empty_dependencies() -> ChainDependencySet:
    """A process with no database: nothing is composed, every stage names a port."""

    return build_migration_chain_dependencies(session_factory=None)


def complete_dependencies() -> ChainDependencies:
    """A dependency set in which *no* field is ``None``.

    The shape a host with a resolved tenant, a run context and the M06 facade
    would hand to ``assemble_migration_chain``. Used only to prove the resource
    does not require any particular field to be absent.
    """

    from prodavan.application.migration.m06_grants_gateway.composition import (
        ShadowGatewayPorts,
    )

    class _Inert:
        def __getattr__(self, attribute: str) -> Any:
            def refuse(*_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("an assembled port was executed during assembly")

            return refuse

    from prodavan.application.migration.m04e_definition_version.repository import (
        ServerDefinitionVersionRepository,
    )

    inert = _Inert()
    return ChainDependencies(
        session_factory=RefusingSessionFactory(),
        m04_tenant_context=inert,
        m04e_reader=inert,
        m04e_store=inert,
        m04e_gates=inert,
        m04e_proofs=inert,
        m04e_artifacts=inert,
        m04e_server_repository=ServerDefinitionVersionRepository(inert),
        m04e_m05_handoff=inert,
        m04e_page_size=100,
        m05_tenant_context=inert,
        m05_runtime_context=inert,
        m05_upstream_evidence=inert,
        m05_promotion_records=inert,
        m05_identity_sessions=inert,
        m05_page_size=100,
        m06_ports=ShadowGatewayPorts(
            company=inert,
            m04_scope=inert,
            m04e_definition_versions=ServerDefinitionVersionRepository(inert),
            m05_instances=inert,
            m05_upstream=inert,
            handoff_partitions=inert,
            legacy_repository=inert,
            legacy_scope=inert,
            legacy_quota=inert,
            canonical_grants=inert,
            method_mappings=inert,
            method_servers={},
            method_versions={},
            evidence_read=inert,
            evidence_transaction=inert,
        ),
        m06_parity_observations=inert,
        m06_parity_summaries=inert,
        m06_parity_reports=inert,
        cutover_repository=inert,
        cutover_gate_verifier=inert,
        cutover_legacy_owner_verifier=inert,
        cutover_rollback_verifier=inert,
        cutover_canonical_repair_verifier=inert,
    )


def complete_dependency_set() -> ChainDependencySet:
    return ChainDependencySet(
        dependencies=complete_dependencies(),
        built=("complete",),
        blockers=(),
    )


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


def wired_chain() -> MigrationChain:
    """A chain where every stage reports ``wired`` — the health predicate's input.

    Built from the real value types (``StageAssembly`` / ``MigrationChain``, the
    latter validating the reviewed order), not from a mock: what is fabricated is
    only the *outcome* of assembly, and that is the point of this fixture. Today's
    composition root cannot produce it (``m06_partition_handoff_read_port_unimplemented``
    keeps M06 unwired); ``test_assembled_chain`` pins that real answer.
    """

    stages = tuple(
        StageAssembly(
            stage=spec.stage,
            services=(("assembled_service", object()),),
            blockers=(),
        )
        for spec in MIGRATION_SEQUENCE
    )
    return MigrationChain(stages=stages)


def unwired_chain() -> MigrationChain:
    """A chain where every stage is blocked by one named code."""

    stages = tuple(
        StageAssembly(
            stage=spec.stage,
            services=(),
            blockers=(
                # ``AssemblyBlocker`` is the real blocker type, imported lazily so the
                # fixture stays readable next to the chain it explains.
                _blocker(spec.stage, "test_stage_blocked"),
            ),
        )
        for spec in MIGRATION_SEQUENCE
    )
    return MigrationChain(stages=stages)


def _blocker(stage: MigrationStage, code: str) -> Any:
    from prodavan.application.migration.composition import AssemblyBlocker

    return AssemblyBlocker(stage=stage, code=code, port=None, detail="fixture")


# ---------------------------------------------------------------------------
# App / manager / resource plumbing
# ---------------------------------------------------------------------------


def build_app() -> FastAPI:
    """A bare app: only ``app.state`` is used, so no router or DB is needed."""

    return FastAPI()


def resource(
    *,
    enabled: bool,
    dependency_builder: Any = None,
    chain_builder: Any = None,
) -> MigrationChainResource:
    return MigrationChainResource(
        enabled=enabled,
        dependency_builder=dependency_builder,
        chain_builder=chain_builder,
    )


def manager_with(res: MigrationChainResource) -> LifespanManager:
    return LifespanManager().register(res)


def chain_state(app: FastAPI) -> Any:
    return getattr(app.state, STATE_ATTR, None)


def set_chain_state(app: FastAPI, value: Any) -> None:
    setattr(app.state, STATE_ATTR, value)


__all__ = [
    "RefusingSessionFactory",
    "build_app",
    "chain_state",
    "complete_dependencies",
    "complete_dependency_set",
    "empty_dependencies",
    "full_dependencies",
    "manager_with",
    "resource",
    "set_chain_state",
    "unwired_chain",
    "wired_chain",
]
