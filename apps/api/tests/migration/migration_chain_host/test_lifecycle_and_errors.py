"""Lifecycle symmetry and the "a composition error is not an outage" rule.

Two contracts live here:

* **shutdown clears everything** — the published chain, the dependency set and the
  app reference. A stopped process must not keep an object graph whose
  session-bound stores belong to a disposed engine, and it must not delete a chain
  somebody else published on the same attribute.
* **a composition error is data** — ``assemble_migration_chain`` raising, or the
  dependency resolver raising, leaves the process running with
  ``health() is False`` and one logged code. A diagnostic resource that can
  crash-loop the API would be worse than no resource at all.
"""

from __future__ import annotations

from prodavan.core.infra.migration_chain_resource import (
    BLOCKER_COMPOSITION_FAILED,
    get_migration_chain,
)

from .acceptance_fixtures import (
    build_app,
    chain_state,
    empty_dependencies,
    full_dependencies,
    manager_with,
    resource,
    set_chain_state,
    wired_chain,
)

# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


async def test_shutdown_clears_the_chain_and_the_dependencies() -> None:
    app = build_app()
    res = resource(enabled=True, dependency_builder=full_dependencies)

    async with manager_with(res).lifespan(app):
        assert res.chain is not None
        assert res.dependencies is not None
        assert res.built
        assert get_migration_chain(app) is not None

    assert res.chain is None
    assert res.dependencies is None
    assert res.built == ()
    assert res.composition_blockers == ()
    assert res.startup_blocker is None
    assert chain_state(app) is None
    assert get_migration_chain(app) is None
    report = res.report()
    assert report.assembled is False
    assert report.stages == ()
    # Enabled but nothing assembled: unhealthy, not "not applicable".
    assert await res.health() is False


async def test_shutdown_before_startup_is_a_no_op() -> None:
    res = resource(enabled=True, dependency_builder=full_dependencies)
    await res.shutdown()  # must not raise
    assert res.chain is None
    assert await res.health() is False


async def test_shutdown_does_not_delete_a_chain_it_does_not_own() -> None:
    """A foreign chain on the same attribute is never removed by our shutdown."""
    foreign = wired_chain()
    app = build_app()
    res = resource(enabled=True, dependency_builder=full_dependencies)

    async with manager_with(res).lifespan(app):
        set_chain_state(app, foreign)
        assert get_migration_chain(app) is foreign

    assert get_migration_chain(app) is foreign
    assert res.chain is None


async def test_restart_publishes_a_fresh_chain() -> None:
    app = build_app()
    res = resource(enabled=True, dependency_builder=full_dependencies)

    async with manager_with(res).lifespan(app):
        first = get_migration_chain(app)
    assert first is not None and get_migration_chain(app) is None

    async with manager_with(res).lifespan(app):
        second = get_migration_chain(app)
    assert second is not None
    assert second is not first


async def test_startup_without_bind_app_still_composes() -> None:
    """A plain ``LifespanManager``-less start must not lose the report.

    Only *publishing* needs the app handle; the diagnostics do not. Losing them
    because no app was bound would make the resource silently useless in tests and
    in hosts that start it directly.
    """

    res = resource(enabled=True, dependency_builder=full_dependencies)
    await res.startup()
    try:
        assert res.chain is not None
        assert res.report().assembled is True
        assert await res.health() is False
    finally:
        await res.shutdown()


async def test_rebind_to_another_app_does_not_inherit_the_old_state() -> None:
    first = build_app()
    second = build_app()
    res = resource(enabled=True, dependency_builder=full_dependencies)

    res.bind_app(first)
    await res.startup()
    assert get_migration_chain(first) is not None

    res.bind_app(second)
    # The first app's state is withdrawn the moment a different app is bound.
    assert get_migration_chain(first) is None
    assert get_migration_chain(second) is None
    await res.shutdown()


# ---------------------------------------------------------------------------
# Composition errors are data, never outages
# ---------------------------------------------------------------------------


async def test_dependency_resolution_failure_does_not_crash_startup() -> None:
    def builder():
        raise RuntimeError("dependency resolution exploded")

    app = build_app()
    res = resource(enabled=True, dependency_builder=builder)
    async with manager_with(res).lifespan(app):
        # The API is up; only this diagnostic is unavailable.
        assert res.startup_blocker == BLOCKER_COMPOSITION_FAILED
        assert res.chain is None
        assert chain_state(app) is None
        assert await res.health() is False
        snapshot = res.report().snapshot()
        assert snapshot["startup_blocker"] == BLOCKER_COMPOSITION_FAILED
        assert snapshot["blockers"] == [BLOCKER_COMPOSITION_FAILED]

    assert res.startup_blocker is None
    assert res.composition_blockers == ()


async def test_chain_assembly_failure_does_not_crash_startup() -> None:
    """``assemble_migration_chain`` raising is the same rule."""

    def chain_builder(_dependencies):
        raise ValueError("chain assembly exploded")

    app = build_app()
    res = resource(
        enabled=True,
        dependency_builder=empty_dependencies,
        chain_builder=chain_builder,
    )
    async with manager_with(res).lifespan(app):
        assert res.startup_blocker == BLOCKER_COMPOSITION_FAILED
        assert res.chain is None
        assert await res.health() is False
        assert BLOCKER_COMPOSITION_FAILED in res.report().blockers

    assert get_migration_chain(app) is None


async def test_failure_code_is_logged_without_the_exception_message(caplog) -> None:
    """Only a class-derived code is logged; a message can embed a DSN."""

    def builder():
        raise RuntimeError("postgresql://user:secret@db/prod")

    app = build_app()
    res = resource(enabled=True, dependency_builder=builder)
    with caplog.at_level("ERROR"):
        async with manager_with(res).lifespan(app):
            pass

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert BLOCKER_COMPOSITION_FAILED in text
    assert "runtimeerror" in text
    assert "secret" not in text
    assert "postgresql://" not in text


async def test_report_and_snapshot_never_carry_a_location_or_a_secret() -> None:
    res = resource(enabled=True, dependency_builder=full_dependencies)
    await res.startup()
    try:
        report = res.report()
        snapshot = report.snapshot()
    finally:
        await res.shutdown()

    for blob in (repr(report), repr(snapshot), str(report.blockers)):
        for forbidden in ("postgresql://", "postgres://", "password", "secret", "asyncpg"):
            assert forbidden not in blob.lower()


async def test_disabled_flag_after_a_failed_start_clears_the_code() -> None:
    """A later disabled start must not keep a stale failure visible."""

    def builder():
        raise RuntimeError("boom")

    res = resource(enabled=True, dependency_builder=builder)
    await res.startup()
    assert res.startup_blocker == BLOCKER_COMPOSITION_FAILED

    disabled = resource(enabled=False, dependency_builder=builder)
    await disabled.startup()
    assert disabled.startup_blocker is None
    assert disabled.composition_blockers == ()
    assert await disabled.health() is None
    await res.shutdown()
    await disabled.shutdown()


async def test_empty_dependency_set_is_still_assembled_not_failed() -> None:
    """``session_factory=None`` is a stated dependency, not a failure."""

    res = resource(enabled=True, dependency_builder=empty_dependencies)
    await res.startup()
    try:
        assert res.startup_blocker is None
        assert res.chain is not None
        assert res.report().assembled is True
        assert res.report().stages
        assert await res.health() is False
    finally:
        await res.shutdown()
