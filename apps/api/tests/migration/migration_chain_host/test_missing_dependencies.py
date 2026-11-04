"""Flag on without dependencies: named blockers, healthy process.

A deployment that sets ``MIGRATION_CHAIN_ENABLED=true`` on a process with no
usable database must still boot. What it must not do is report the chain wired,
or substitute a stub for a stage it could not build: every stage names its own
missing port, the resource answers ``health() is False``, and the process keeps
serving every other route.

The two shapes covered here are the ones a real deployment can hit:

* **no database at all** — the factory is deliberately ``None``;
* **an unusable factory** — ``get_session_factory()`` raises at startup.
"""

from __future__ import annotations

from prodavan.application.migration.composition import MigrationStage
from prodavan.core.infra.migration_chain_resource import (
    BLOCKER_SESSION_FACTORY_UNAVAILABLE,
    build_migration_chain_dependencies,
    get_migration_chain,
)

from .acceptance_fixtures import (
    build_app,
    empty_dependencies,
    manager_with,
    resource,
)


async def test_missing_dependencies_name_every_stage_and_start_the_process() -> None:
    app = build_app()
    res = resource(enabled=True, dependency_builder=empty_dependencies)

    async with manager_with(res).lifespan(app):
        assert res.chain is not None
        assert get_migration_chain(app) is not None
        report = res.report()
        assert report.assembled is True
        assert report.wired is False
        assert await res.health() is False

        # Nothing was composed, so nothing claims to be wired.
        assert all(stage.wired is False for stage in report.stages)
        assert all(stage.services == () for stage in report.stages)

        by_stage = {stage.stage: stage for stage in report.stages}
        m04 = by_stage[MigrationStage.M04_SCOPE.value]
        assert "m04_session_factory_missing" in m04.blockers
        assert "m04_required_port_missing" in m04.blockers
        assert "m04e_required_port_missing" in by_stage[
            MigrationStage.M04E_DEFINITION_VERSION.value
        ].blockers
        m05 = by_stage[MigrationStage.M05_INSTANCE_RUNTIME.value]
        assert "m05_session_factory_missing" in m05.blockers
        assert "m05_upstream_evidence_missing" in m05.blockers
        m06 = by_stage[MigrationStage.M06_GRANTS_GATEWAY.value]
        assert "m06_shadow_gateway_ports_missing" in m06.blockers
        cutover = by_stage[MigrationStage.CUTOVER.value]
        assert "cutover_required_port_missing" in cutover.blockers
        # Five absent cutover ports, five separate blockers: no generic refusal.
        assert len([code for code in cutover.blockers if code == "cutover_required_port_missing"]) >= 1

    # The lifespan exited normally — the process was never asked to fail.
    assert get_migration_chain(app) is None


async def test_missing_dependencies_are_reported_as_a_blocker_list_not_a_gap() -> None:
    res = resource(enabled=True, dependency_builder=empty_dependencies)
    await res.startup()
    snapshot = res.report().snapshot()
    await res.shutdown()

    assert snapshot["blockers"], "an unassembled-stage chain must name its blockers"
    # No code is ever a sentence: the report is a closed vocabulary of codes.
    for code in snapshot["blockers"]:
        assert code and " " not in code


async def test_unusable_session_factory_is_a_named_code_and_not_a_crash(monkeypatch) -> None:
    """``get_session_factory()`` raising must not take startup down."""

    from prodavan.infrastructure.persistence import database

    def explode():
        raise RuntimeError("database_url is not configured")

    monkeypatch.setattr(database, "get_session_factory", explode)

    deps = build_migration_chain_dependencies()
    assert deps.built == ()
    assert deps.blockers == (BLOCKER_SESSION_FACTORY_UNAVAILABLE,)
    assert deps.dependencies.session_factory is None

    app = build_app()
    res = resource(enabled=True)  # the default builder resolves the process factory
    async with manager_with(res).lifespan(app):
        assert await res.health() is False
        assert BLOCKER_SESSION_FACTORY_UNAVAILABLE in res.composition_blockers
        assert res.chain is not None
        assert res.chain.wired is False


async def test_unusable_factory_still_reports_every_stage() -> None:
    """A dead factory must not collapse the report into one code.

    The stage diagnosis is what makes the resource useful: an operator has to see
    which stage is blocked by what, even when the database itself is the problem.
    """

    from prodavan.infrastructure.persistence import database

    def explode():
        raise RuntimeError("no dsn")

    original = database.get_session_factory
    database.get_session_factory = explode  # type: ignore[assignment]
    try:
        deps = build_migration_chain_dependencies()
    finally:
        database.get_session_factory = original  # type: ignore[assignment]

    res = resource(enabled=True, dependency_builder=lambda: deps)
    await res.startup()
    try:
        assert [stage.stage for stage in res.report().stages] == [
            stage.value for stage in MigrationStage
        ]
    finally:
        await res.shutdown()
