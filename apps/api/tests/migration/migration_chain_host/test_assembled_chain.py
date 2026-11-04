"""Flag on: the chain really is assembled from the tree, and nothing is started.

Three levels are asserted, from the most faithful to the most diagnostic:

* **process-shaped dependencies** — everything a process can legitimately own is
  resolved from the real ports (M04E reader/gates/proofs, the M06 durable
  evidence stores, the five cutover ports) and the chain comes back with those
  stages populated;
* **a complete dependency set** (no field ``None``) — every stage except the one
  carrying the registered review gap is wired, which proves the resource's own
  dependency resolution is the only thing standing between the process and a
  wired chain;
* **a reported-wired chain** — the health predicate, which is ``True`` only when
  *every* stage reports wired.

In all three, the session factory is never called: assembly composes, it does not
open a transaction, and no stage method runs.
"""

from __future__ import annotations

import json

from prodavan.application.migration.composition import MigrationStage
from prodavan.core.infra.migration_chain_resource import (
    get_migration_chain,
)
from prodavan.core.lifespan.manager import LifespanManager

from .acceptance_fixtures import (
    RefusingSessionFactory,
    build_app,
    chain_state,
    complete_dependency_set,
    full_dependencies,
    manager_with,
    resource,
    wired_chain,
)

# ---------------------------------------------------------------------------
# Process-shaped dependencies: the real composition root
# ---------------------------------------------------------------------------


async def test_full_dependency_set_assembles_every_stage_and_publishes_it() -> None:
    factory = RefusingSessionFactory()
    app = build_app()
    res = resource(enabled=True, dependency_builder=lambda: full_dependencies(factory))

    async with manager_with(res).lifespan(app):
        chain = get_migration_chain(app)
        assert chain is not None
        assert chain is chain_state(app)
        assert chain is res.chain
        # Reviewed order, all five stages present.
        assert tuple(assembly.stage for assembly in chain.stages) == tuple(MigrationStage)

        report = res.report()
        assert report.assembled is True
        assert [stage.stage for stage in report.stages] == [
            stage.value for stage in MigrationStage
        ]
        # The four upstream stages carry named blockers, because a process cannot
        # own a tenant principal, a run context or the M06 facade. Cutover is the
        # one whose five ports are all implemented — and it reports wired, so the
        # report cannot be mistaken for "nothing works yet".
        by_stage = {stage.stage: stage for stage in report.stages}
        for name in (
            "M04_scope",
            "M04E_definition_version",
            "M05_runtime",
            "M06_grants_gateway",
        ):
            assert by_stage[name].blockers, name
            assert by_stage[name].wired is False, name
        assert by_stage["cutover"].wired is True
        assert by_stage["cutover"].blockers == ()

        # Assembly is not execution: no session was ever opened.
        assert factory.opened == 0

    assert get_migration_chain(app) is None


async def test_process_dependencies_build_the_ports_that_can_be_built() -> None:
    """The resolver constructs what it can and names only what it cannot."""

    factory = RefusingSessionFactory()
    deps = full_dependencies(factory)

    assert deps.built == (
        "m04e_sql_ports",
        "m06_durable_evidence_ports",
        "cutover_ports",
    )
    dependencies = deps.dependencies
    # Ports that only need a factory are composed…
    assert dependencies.m04e_reader is not None
    assert dependencies.m04e_gates is not None
    assert dependencies.m04e_proofs is not None
    assert dependencies.m06_parity_observations is not None
    assert dependencies.m06_parity_summaries is not None
    assert dependencies.m06_parity_reports is not None
    assert dependencies.cutover_repository is not None
    assert dependencies.cutover_gate_verifier is not None
    assert dependencies.cutover_legacy_owner_verifier is not None
    assert dependencies.cutover_rollback_verifier is not None
    assert dependencies.cutover_canonical_repair_verifier is not None
    # …and the ones that cannot are ``None`` with a code, never a stub.
    for absent in (
        "m04_tenant_context",
        "m04e_store",
        "m04e_artifacts",
        "m04e_server_repository",
        "m04e_m05_handoff",
        "m05_tenant_context",
        "m05_runtime_context",
        "m05_upstream_evidence",
        "m05_promotion_records",
        "m06_ports",
    ):
        assert getattr(dependencies, absent) is None, absent
    assert factory.opened == 0


async def test_cutover_stage_is_wired_because_its_ports_are_implemented() -> None:
    """The resource must not block a stage whose ports exist.

    ``build_cutover_ports`` supplies all five dependencies, so the cutover stage
    reports ``wired`` even in a process-shaped graph. A diagnostic resource that
    reported it unwired would hide a working binding behind an invented blocker.
    """

    app = build_app()
    res = resource(enabled=True, dependency_builder=full_dependencies)

    async with manager_with(res).lifespan(app):
        report = res.report()
        cutover = next(stage for stage in report.stages if stage.stage == "cutover")
        assert cutover.wired is True
        assert cutover.blockers == ()
        assert "cutover_command_service" in cutover.services


async def test_the_m06_registry_gap_is_reported_not_smoothed_over() -> None:
    """The one blocker no process can close by itself stays visible by name.

    ``composition.UNIMPLEMENTED_PORTS`` keeps ``PartitionHandoffReadPort`` until
    the M06 stage builder takes it as a dependency, so the M06 stage reports that
    exact code. The resource must not report the chain wired around it.
    """

    app = build_app()
    res = resource(enabled=True, dependency_builder=full_dependencies)

    async with manager_with(res).lifespan(app):
        report = res.report()
        m06 = next(stage for stage in report.stages if stage.stage == "M06_grants_gateway")
        assert m06.wired is False
        assert "m06_partition_handoff_read_port_unimplemented" in m06.blockers
        assert "m06_shadow_gateway_ports_missing" in m06.blockers
        assert report.wired is False
        # The process still boots and the report is still complete.
        assert await res.health() is False


async def test_process_scope_codes_explain_the_tenant_and_run_bound_absences() -> None:
    app = build_app()
    res = resource(enabled=True, dependency_builder=full_dependencies)

    async with manager_with(res).lifespan(app):
        codes = res.composition_blockers
        assert "migration_chain_evidence_scope_is_per_partition" in codes
        assert "migration_chain_runtime_context_is_per_run" in codes
        assert "migration_chain_shadow_gateway_ports_unavailable" in codes
        assert "migration_chain_m04_tenant_context_unavailable" in codes


# ---------------------------------------------------------------------------
# Complete dependency set: only the registered gap remains
# ---------------------------------------------------------------------------


async def test_complete_dependency_set_wires_every_stage_but_the_registered_gap() -> None:
    """No field is ``None``: the resource's own resolution is not the blocker.

    This is the sharpest statement of where the remaining work is. Four stages
    become wired; the fifth is held back by the review §3 registry entry alone —
    not by anything this resource failed to resolve.
    """

    app = build_app()
    res = resource(enabled=True, dependency_builder=complete_dependency_set)

    async with manager_with(res).lifespan(app):
        chain = res.chain
        assert chain is not None
        unwired = [assembly.stage for assembly in chain.stages if not assembly.wired]
        assert unwired == [MigrationStage.M06_GRANTS_GATEWAY]
        # No process-scope code is invented when the host states every dependency.
        assert res.composition_blockers == ()
        assert await res.health() is False


# ---------------------------------------------------------------------------
# The health predicate
# ---------------------------------------------------------------------------


async def test_health_is_true_only_when_every_stage_reports_wired() -> None:
    """``health()`` is ``all(stage.wired)`` — not "assembled", not "some stage".

    The state pinned here cannot be produced by today's composition root (see
    :func:`test_the_m06_registry_gap_is_reported_not_smoothed_over`); the test
    exists so the predicate itself cannot silently become weaker than the
    documented contract "True if all stages are wired".
    """

    from prodavan.core.infra.migration_chain_resource import ChainDependencySet

    chain = wired_chain()
    # Dependencies are irrelevant when the test states the chain builder; only the
    # *absence* of process-scope blockers matters for the predicate.
    dependencies = full_dependencies().dependencies
    app = build_app()
    res = resource(
        enabled=True,
        dependency_builder=lambda: ChainDependencySet(dependencies=dependencies, blockers=()),
        chain_builder=lambda _dependencies: chain,
    )

    async with manager_with(res).lifespan(app):
        assert chain_state(app) is chain
        assert await res.health() is True
        report = res.report()
        assert report.wired is True
        assert report.assembled is True
        assert len(report.stages) == 5
        assert all(stage.wired for stage in report.stages)


# ---------------------------------------------------------------------------
# Report surface
# ---------------------------------------------------------------------------


async def test_report_is_json_safe_and_names_stages_and_services() -> None:
    app = build_app()
    res = resource(enabled=True, dependency_builder=full_dependencies)

    async with manager_with(res).lifespan(app):
        snapshot = res.report().snapshot()

    # JSON-safe: no callable, no port instance, no exception object.
    encoded = json.dumps(snapshot, sort_keys=True)
    assert "RefusingSessionFactory" not in encoded
    assert "object at 0x" not in encoded

    assert snapshot["enabled"] is True
    assert snapshot["assembled"] is True
    assert snapshot["wired"] is False
    names = [stage["stage"] for stage in snapshot["stages"]]
    assert names == ["M04_scope", "M04E_definition_version", "M05_runtime", "M06_grants_gateway", "cutover"]
    # At least one stage names the service the builder actually constructed.
    assert any(stage["services"] for stage in snapshot["stages"])


async def test_report_lists_blockers_per_stage_in_the_composition_vocabulary() -> None:
    app = build_app()
    res = resource(enabled=True, dependency_builder=full_dependencies)

    async with manager_with(res).lifespan(app):
        report = res.report()

    m04 = next(stage for stage in report.stages if stage.stage == "M04_scope")
    assert "m04_required_port_missing" in m04.blockers
    m04e = next(stage for stage in report.stages if stage.stage == "M04E_definition_version")
    assert "m04e_artifact_content_port_missing" in m04e.blockers
    assert "m04e_server_definition_repository_missing" in m04e.blockers
    m05 = next(stage for stage in report.stages if stage.stage == "M05_runtime")
    assert "m05_upstream_evidence_missing" in m05.blockers
    assert "m05_runtime_context_missing" in m05.blockers
    # Every blocker is a code, never a message: no detail text leaks into the report.
    for stage in report.stages:
        for code in stage.blockers:
            assert " " not in code, code
            assert ":" not in code, code


async def test_resource_is_healthy_in_the_manager_report_only_when_wired() -> None:
    manager = LifespanManager().register(
        resource(enabled=True, dependency_builder=full_dependencies)
    )
    app = build_app()
    async with manager.lifespan(app):
        report = await manager.health_report()
    assert report["migration_chain"] is False
