"""Flag off: the registered resource is completely inert.

The resource is registered in the delivery lifespan of every deployment, so
"registered" must not mean "does something". These tests pin that down four ways:
no dependency builder call, no session factory resolution, nothing published on
``app.state``, and ``health()`` answering "not applicable" rather than dragging
the readiness probe down.
"""

from __future__ import annotations

import inspect

import pytest

from prodavan.config.settings import Settings, settings
from prodavan.core.infra.migration_chain_resource import (
    RESOURCE_NAME,
    STATE_ATTR,
    MigrationChainResource,
    migration_chain_resource_from_settings,
)

from .acceptance_fixtures import (
    RefusingSessionFactory,
    build_app,
    chain_state,
    full_dependencies,
    manager_with,
    resource,
)


async def test_disabled_startup_composes_nothing() -> None:
    factory = RefusingSessionFactory()
    calls: list[int] = []

    def builder():
        calls.append(1)
        return full_dependencies(factory)

    app = build_app()
    res = resource(enabled=False, dependency_builder=builder)
    async with manager_with(res).lifespan(app):
        assert chain_state(app) is None
        assert res.chain is None
        assert res.dependencies is None
        assert res.report().assembled is False
        # health() is "not applicable" — a disabled diagnostic resource must not
        # make the process look unhealthy.
        assert await res.health() is None

    # The builder is never called: with the flag off the process must behave as
    # if this module did not exist.
    assert calls == []
    # And no port constructor was given the factory, so it was never even called.
    assert factory.opened == 0
    assert chain_state(app) is None


async def test_disabled_startup_resolves_no_session_factory() -> None:
    """The default dependency builder itself is not reached while the flag is off."""

    calls: list[int] = []

    def builder():  # pragma: no cover - must never run
        calls.append(1)
        raise AssertionError("the dependency builder must not run while disabled")

    res = MigrationChainResource(enabled=False, dependency_builder=builder)
    await res.startup()
    await res.shutdown()

    assert calls == []
    assert await res.health() is None
    assert res.report().snapshot() == {
        "enabled": False,
        "assembled": False,
        "wired": False,
        "startup_blocker": None,
        "built": [],
        "composition_blockers": [],
        "blockers": [],
        "stages": [],
    }


async def test_disabled_shutdown_is_a_no_op() -> None:
    res = resource(enabled=False)
    await res.shutdown()
    assert res.chain is None
    assert await res.health() is None


async def test_disabled_resource_never_fails_health_in_a_report() -> None:
    """A manager report keeps ``None`` for this resource, exactly like before."""

    from prodavan.core.lifespan.manager import LifespanManager

    manager = LifespanManager().register(resource(enabled=False))
    report = await manager.health_report()
    assert report[RESOURCE_NAME] is None


async def test_flag_is_off_by_default_in_settings() -> None:
    field = Settings.model_fields["migration_chain_enabled"]
    assert field.default is False
    assert settings.migration_chain_enabled is False
    assert RESOURCE_NAME == "migration_chain"
    assert STATE_ATTR == "migration_chain"


def test_resource_declares_the_lifespan_contract() -> None:
    res = MigrationChainResource()
    assert res.name == RESOURCE_NAME
    assert res.enabled is False
    for method in ("startup", "shutdown", "health"):
        assert inspect.iscoroutinefunction(getattr(res, method))


def test_from_settings_projects_the_flag() -> None:
    """The factory wiring uses reads the process setting, not a hard-coded value."""

    res = migration_chain_resource_from_settings()
    assert res.enabled is settings.migration_chain_enabled is False


def test_duplicate_registration_is_refused() -> None:
    """Two chain resources would report two different truths; the manager refuses."""

    from prodavan.core.lifespan.manager import LifespanManager

    manager = LifespanManager().register(resource(enabled=False))
    with pytest.raises(ValueError):
        manager.register(resource(enabled=False))
