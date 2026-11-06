"""Install/restore of the host provider: idempotent, and fail-closed when withdrawn.

``AgentRunWorkerResource`` resolves ``current_host_dependency_provider()`` once per
start and has no way to tell "nobody installed anything" from "the composition is
empty" — both must idle.  That makes the seam itself the contract under test:

* installing publishes exactly the bundle that was built (one instance, no rebuild
  per provider call — a second lease store per process would double every fence);
* restoring puts back the **pristine** fail-closed default (identity with
  ``unavailable_host_dependencies``), which is what proves that enabling a flag
  alone starts nothing;
* both directions are idempotent, and an install after a restore works again.

Every test restores the seam before it returns, so the module state cannot leak
into the next test (or into the host suite).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from prodavan.core.infra.agent_run_bundle import (
    AgentRunBundle,
    agent_run_host_dependencies_installed,
    build_agent_run_bundle,
    install_agent_run_bundle,
    install_agent_run_host_dependencies,
    installed_agent_run_bundle,
    restore_agent_run_host_dependencies,
)
from prodavan.core.infra.agent_run_worker_resource import (
    BLOCKER_DEPENDENCIES_UNWIRED,
    AgentRunHostDependencies,
    AgentRunHostSettings,
    AgentRunWorkerResource,
    current_host_dependency_provider,
    unavailable_host_dependencies,
)

from .acceptance_fixtures import FakeSessionFactory

ENABLED = AgentRunHostSettings(worker_enabled=True, consumer_enabled=True)


@pytest.fixture(autouse=True)
def _clean_seam() -> Iterator[None]:
    """Neither direction of the seam may leak between tests."""
    restore_agent_run_host_dependencies()
    yield
    restore_agent_run_host_dependencies()


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def test_install_publishes_the_built_bundle_on_the_seam() -> None:
    bundle = install_agent_run_host_dependencies(session_factory=FakeSessionFactory())

    assert isinstance(bundle, AgentRunBundle)
    assert bundle.wired is True
    assert agent_run_host_dependencies_installed() is True
    assert installed_agent_run_bundle() is bundle
    # The provider answers with the very dependencies the host resource consumes.
    assert current_host_dependency_provider()() is bundle.dependencies


def test_the_provider_never_rebuilds_on_each_call() -> None:
    """A rebuild per provider call would create a second lease store in one process."""
    bundle = install_agent_run_host_dependencies(session_factory=FakeSessionFactory())
    provider = current_host_dependency_provider()

    first = provider()
    second = provider()

    assert first is second is bundle.dependencies


def test_install_is_idempotent_for_the_same_bundle() -> None:
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())

    install_agent_run_bundle(bundle)
    install_agent_run_bundle(bundle)

    assert installed_agent_run_bundle() is bundle
    assert current_host_dependency_provider()() is bundle.dependencies
    assert agent_run_host_dependencies_installed() is True


def test_a_later_install_replaces_the_previous_bundle() -> None:
    first = install_agent_run_host_dependencies(session_factory=FakeSessionFactory())
    second = install_agent_run_host_dependencies(session_factory=FakeSessionFactory())

    assert first is not second
    assert installed_agent_run_bundle() is second
    assert current_host_dependency_provider()() is second.dependencies


def test_install_refuses_anything_that_is_not_a_bundle() -> None:
    """A duck-typed object would publish a composition nobody validated."""
    with pytest.raises(TypeError):
        install_agent_run_bundle(object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def test_restore_returns_the_pristine_fail_closed_default() -> None:
    install_agent_run_host_dependencies(session_factory=FakeSessionFactory())

    restore_agent_run_host_dependencies()

    assert current_host_dependency_provider() is unavailable_host_dependencies
    assert agent_run_host_dependencies_installed() is False
    assert installed_agent_run_bundle() is None
    # Byte-for-byte the default bundle: every member absent, nothing wired.
    assert current_host_dependency_provider()() == AgentRunHostDependencies()
    assert current_host_dependency_provider()().any_wired is False


def test_restore_is_idempotent() -> None:
    restore_agent_run_host_dependencies()
    restore_agent_run_host_dependencies()

    assert current_host_dependency_provider() is unavailable_host_dependencies


def test_install_after_restore_works_again() -> None:
    install_agent_run_host_dependencies(session_factory=FakeSessionFactory())
    restore_agent_run_host_dependencies()

    bundle = install_agent_run_host_dependencies(session_factory=FakeSessionFactory())

    assert bundle.wired is True
    assert agent_run_host_dependencies_installed() is True


async def test_restored_seam_makes_an_enabled_resource_idle() -> None:
    """The point of the restore: an enabled flag with nothing installed claims nothing."""
    restore_agent_run_host_dependencies()
    factory = FakeSessionFactory()
    resource = AgentRunWorkerResource(config=ENABLED, session_factory=factory)

    round_result = await resource.run_worker_round_once()

    assert round_result["skipped"] is True
    assert round_result["reason"] == BLOCKER_DEPENDENCIES_UNWIRED
    assert factory.opened == 0


async def test_installed_seam_is_what_an_enabled_resource_resolves() -> None:
    """An installed bundle reaches a resource that was constructed without one.

    The round is deliberately limited to the two loops' *dependency resolution*:
    with a bundle installed the worker loop is no longer blocked by
    ``agent_run_host_dependencies_unwired``, which is precisely the state the whole
    task exists to reach.  (The tick itself is not driven here — it would need a
    database; the worker/consumer paths are covered by the host suite.)
    """
    bundle = install_agent_run_host_dependencies(session_factory=FakeSessionFactory())
    resource = AgentRunWorkerResource(config=ENABLED)

    # Same provider the resource will consult, and it now answers with a real bundle.
    resolved = current_host_dependency_provider()()
    assert resolved is bundle.dependencies
    assert resolved.any_wired is True
    assert resolved.worker_blockers == ()
    # Nothing was started by installing: the loops still need their own flags.
    assert resource.enabled is True
    assert resource.report()["started"] is False
