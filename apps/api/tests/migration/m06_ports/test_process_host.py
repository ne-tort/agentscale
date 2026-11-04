"""The process host wires the assembly and reports the exact M06 hole.

Task ``MIG-M06-PORTS-01``.

Two contracts:

* ``build_migration_chain_dependencies`` **calls** ``build_shadow_ports``, so the
  M06 port set is composed (not merely mentioned) at startup, and every hole it
  finds reaches the report as its own code;
* at process scope the set is still incomplete — a process owns no partition, no
  snapshot's upstream evidence, no caller-owned SERIALIZABLE session and no
  per-invocation quota — so ``m06_ports`` stays ``None`` and the blanket
  ``migration_chain_shadow_gateway_ports_unavailable`` still names the stage-level
  fact while the per-port codes say *why*.
"""

from __future__ import annotations

from prodavan.application.migration.composition import MigrationStage
from prodavan.application.migration.m06_grants_gateway.ports_assembly import (
    BLOCKER_HANDOFF_PARTITIONS_MISSING,
    BLOCKER_LEGACY_QUOTA_UNAVAILABLE,
    BLOCKER_M04E_DEFINITION_VERSIONS_MISSING,
    BLOCKER_M05_UPSTREAM_MISSING,
    BLOCKER_METHOD_CONFIGURATION_MISSING,
    BLOCKER_METHOD_MAPPINGS_MISSING,
)
from prodavan.core.infra.migration_chain_resource import (
    PART_M06_SHADOW_PORTS_UNAVAILABLE,
    build_migration_chain_dependencies,
)

from .acceptance_fixtures import RefusingSessionFactory


def test_the_process_really_composes_the_legacy_and_canonical_ports() -> None:
    """The ports a process *can* own are constructed, and named as built."""

    factory = RefusingSessionFactory()
    deps = build_migration_chain_dependencies(session_factory=factory)

    # The legacy bundle and the shadow plan were both composed…
    assert {"legacy_gateway_ports", "m06_shadow_ports"} <= set(deps.built)
    assert "legacy_gateway_repository" in deps.built
    assert "legacy_gateway_scope" in deps.built
    # …no session was opened while doing it…
    assert factory.opened == 0
    # …and the port set is still honestly absent.
    assert deps.dependencies.m06_ports is None


def test_the_report_names_each_port_a_process_cannot_own() -> None:
    """One blanket code is no longer the whole story: the holes are itemised."""

    deps = build_migration_chain_dependencies(session_factory=RefusingSessionFactory())
    codes = set(deps.blockers)

    assert PART_M06_SHADOW_PORTS_UNAVAILABLE in codes
    # The per-port reasons the assembly discovered.
    for code in (
        BLOCKER_LEGACY_QUOTA_UNAVAILABLE,
        BLOCKER_HANDOFF_PARTITIONS_MISSING,
        BLOCKER_M05_UPSTREAM_MISSING,
        BLOCKER_M04E_DEFINITION_VERSIONS_MISSING,
        BLOCKER_METHOD_MAPPINGS_MISSING,
        BLOCKER_METHOD_CONFIGURATION_MISSING,
    ):
        assert code in codes, code


def test_the_m06_stage_keeps_its_own_blocker_vocabulary() -> None:
    """Stage diagnosis stays in the composition root's words, not the resource's."""

    from prodavan.application.migration.composition import assemble_migration_chain

    deps = build_migration_chain_dependencies(session_factory=RefusingSessionFactory())
    chain = assemble_migration_chain(deps.dependencies)

    m06 = next(
        assembly
        for assembly in chain.stages
        if assembly.stage is MigrationStage.M06_GRANTS_GATEWAY
    )
    assert m06.wired is False
    assert "m06_shadow_gateway_ports_missing" in m06.blocker_codes


def test_an_uncomposable_plan_still_names_the_quota_gap(monkeypatch) -> None:
    """If the plan itself cannot be built, the absence never goes unnamed."""

    import prodavan.application.migration.m06_grants_gateway.ports_assembly as assembly
    from prodavan.core.infra.migration_chain_resource import (
        PART_M06_LEGACY_QUOTA_TABLE_MISSING,
    )

    def explode(**_kwargs):
        raise RuntimeError("assembly exploded")

    # ``build_migration_chain_dependencies`` imports the builder lazily on every
    # call, so patching the source module is the exact seam.
    monkeypatch.setattr(assembly, "build_shadow_ports", explode)

    deps = build_migration_chain_dependencies(session_factory=RefusingSessionFactory())

    assert "migration_chain_m06_shadow_ports_failed" in deps.blockers
    assert PART_M06_LEGACY_QUOTA_TABLE_MISSING in deps.blockers
    assert PART_M06_SHADOW_PORTS_UNAVAILABLE in deps.blockers
    assert deps.dependencies.m06_ports is None


def test_disabled_resource_still_does_nothing() -> None:
    """The flag contract is untouched: nothing is resolved without it."""

    from prodavan.core.infra.migration_chain_resource import MigrationChainResource

    resource = MigrationChainResource(enabled=False)
    assert resource.enabled is False
