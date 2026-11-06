"""A partition can actually wire the M06 stage — the blocker really comes off.

Task ``MIG-M06-PORTS-01``.

A process cannot state the whole port set, which is why
``migration_chain_resource`` keeps ``m06_ports=None``.  A *partition* can: it has
the one fact a process lacks (``(tenant, definition)`` plus a writer generation),
so the handoff reader binds its ``TrustedEvidenceScope`` and every remaining port
is either derived or stated by the operator.

This module proves the end of that road, not just the composition: the assembled
set goes through ``build_partition_chain`` into the reviewed chain assembler and
the M06 stage reports **wired**.  Without that, "the ports build" would still
leave open whether the stage accepts them.
"""

from __future__ import annotations

from prodavan.application.migration.composition import (
    MigrationStage,
    assemble_migration_chain,
)
from prodavan.application.migration.m06_grants_gateway.ports_assembly import (
    build_shadow_ports,
)
from prodavan.application.migration.partition_chain import build_partition_chain

from ..partition_chain.partition_fixtures import (
    GENERATION,
    Inert,
    complete_overrides,
    key,
)
from .acceptance_fixtures import (
    RefusingSessionFactory,
    definition_repository,
    method_configuration,
    method_mappings,
)


def _ports_for_partition(factory: RefusingSessionFactory):
    servers, versions = method_configuration()
    return build_shadow_ports(
        session_factory=factory,
        partition=key(),
        writer_generation=GENERATION,
        m04_tenant_context=Inert(),
        m05_tenant_context=Inert(),
        m04e_definition_versions=definition_repository(),
        m05_upstream=Inert(),
        legacy_quota=Inert(),
        method_mappings=method_mappings(),
        method_servers=servers,
        method_versions=versions,
    )


def test_a_partition_can_compose_the_full_port_set() -> None:
    plan = _ports_for_partition(RefusingSessionFactory())

    assert plan.complete is True, plan.snapshot()
    assert plan.blockers == ()


def test_the_partition_chain_accepts_the_assembled_set_and_wires_m06() -> None:
    """The decisive integration: M06 reports ``wired`` with these ports."""

    factory = RefusingSessionFactory()
    ports = _ports_for_partition(factory).ports
    assert ports is not None

    overrides = complete_overrides(m06_shadow_ports=ports)
    plan = build_partition_chain(
        key=key(),
        session_factory=factory,
        writer_generation=GENERATION,
        **overrides.chain_kwargs(),
    )

    assert plan.dependencies.m06_ports is ports
    assert "m06_ports" in plan.built
    assert plan.blocker_for("m06_shadow_gateway_ports_unavailable") is None

    chain = assemble_migration_chain(plan.dependencies)
    m06 = next(
        assembly
        for assembly in chain.stages
        if assembly.stage is MigrationStage.M06_GRANTS_GATEWAY
    )
    assert m06.wired is True, m06.blocker_codes
    assert m06.blocker_codes == ()
    # The facade the stage built is the shadow one, and it owns nothing.
    gateway = m06.service("shadow_gateway")
    assert gateway is not None
    assert gateway.DECISION_OWNER == "legacy"
    assert gateway.CANONICAL_READ_OWNER == "legacy"
    assert gateway.CANONICAL_POSITIVE_WRITES is False


def test_the_partition_assembly_opens_no_session() -> None:
    factory = RefusingSessionFactory()
    ports = _ports_for_partition(factory).ports
    assert ports is not None

    build_partition_chain(
        key=key(),
        session_factory=factory,
        writer_generation=GENERATION,
        **complete_overrides(m06_shadow_ports=ports).chain_kwargs(),
    )

    assert factory.opened == 0


def test_the_same_partition_assembled_twice_never_shares_a_handoff_reader() -> None:
    """Two assemblies must not hand the same store to two partitions."""

    first = _ports_for_partition(RefusingSessionFactory()).ports
    second = _ports_for_partition(RefusingSessionFactory()).ports

    assert first is not None and second is not None
    assert first.handoff_partitions is not second.handoff_partitions
    assert (
        first.handoff_partitions._boundaries.scope  # noqa: SLF001 - the binding is the point
        is not second.handoff_partitions._boundaries.scope  # noqa: SLF001
    )
