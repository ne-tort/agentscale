"""Removing one member at a time yields a *named* blocker and the rule's ``any_wired``.

The bundle's whole value is that a missing dependency is data, not an exception and
not a stand-in.  Each case below removes exactly one seam and asserts:

* the corresponding member is ``None`` and the corresponding blocker is reported;
* the host's own ``worker_blockers``/``consumer_blockers`` (not a second opinion)
  agree with that report;
* ``any_wired`` follows the resource's rule — one assembled member is enough for
  ``True``, and an entirely empty bundle is the fail-closed default;
* no session was opened while assembling or while idling.

Two cases carry a **derived** consequence by design, and it is asserted rather than
hidden: ``ControlPlaneAgentRunExecutionAdapter`` refuses to exist without a run read
port (it resolves the acting employee through ``get_run``), and a port cannot be
built without a lease store.  So "no runs" and "no lease store" also block the
execution port — the host must not be told a port exists when it cannot.
"""

from __future__ import annotations

import pytest

from prodavan.core.infra.agent_run_bundle import (
    BLOCKER_LEASE_STORE_UNWIRED,
    build_agent_run_bundle,
)
from prodavan.core.infra.agent_run_worker_resource import (
    BLOCKER_DEPENDENCIES_UNWIRED,
    BLOCKER_EXECUTION_PORT_UNWIRED,
    BLOCKER_JOURNAL_UNWIRED,
    BLOCKER_RUN_SOURCE_UNWIRED,
    BLOCKER_TURN_RUNNER_UNWIRED,
    AgentRunHostSettings,
    AgentRunWorkerResource,
)

from .acceptance_fixtures import FakeSessionFactory, member_names

ENABLED = AgentRunHostSettings(
    worker_enabled=True,
    consumer_enabled=True,
    worker_interval_sec=60.0,
    consumer_interval_sec=60.0,
)


# ---------------------------------------------------------------------------
# One member at a time
# ---------------------------------------------------------------------------


def test_missing_execution_port_blocks_both_loops_and_leaves_the_rest_wired() -> None:
    """A port needs a control plane; ``control_plane=None`` declares it unavailable."""
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(), control_plane=None
    )
    report = bundle.report

    assert member_names(bundle) == {
        "execution_port": False,
        "runs": True,
        "journal": True,
        "runner": True,
    }
    assert report.execution_port_blocker == BLOCKER_EXECUTION_PORT_UNWIRED
    assert report.blockers == (BLOCKER_EXECUTION_PORT_UNWIRED,)
    assert report.consumer_blockers == (BLOCKER_EXECUTION_PORT_UNWIRED,)
    assert report.worker_blockers == (BLOCKER_EXECUTION_PORT_UNWIRED,)
    assert report.any_wired is True
    assert report.wired is False
    assert bundle.lease_store is not None


def test_missing_run_source_also_blocks_the_port_that_needs_it() -> None:
    """The adapter resolves the acting employee through ``get_run``; no source, no port."""
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(), runs_factory=None
    )
    report = bundle.report

    assert member_names(bundle) == {
        "execution_port": False,
        "runs": False,
        "journal": True,
        "runner": True,
    }
    assert report.runs_blocker == BLOCKER_RUN_SOURCE_UNWIRED
    # Reported in the fixed order: the port first, then the member that removed it.
    assert report.blockers == (
        BLOCKER_EXECUTION_PORT_UNWIRED,
        BLOCKER_RUN_SOURCE_UNWIRED,
    )
    assert report.any_wired is True
    assert report.wired is False


def test_missing_journal_blocks_only_the_worker() -> None:
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(), journal_factory=None
    )
    report = bundle.report

    assert member_names(bundle) == {
        "execution_port": True,
        "runs": True,
        "journal": False,
        "runner": True,
    }
    assert report.journal_blocker == BLOCKER_JOURNAL_UNWIRED
    assert report.blockers == (BLOCKER_JOURNAL_UNWIRED,)
    # The consumer only needs a port; an unfinished execution port would be a lie,
    # but a missing attempt ledger genuinely does not stop intent *dispatch*.
    assert report.consumer_blockers == ()
    assert report.worker_blockers == (BLOCKER_JOURNAL_UNWIRED,)
    assert report.any_wired is True


def test_missing_turn_runner_blocks_only_the_worker() -> None:
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(), runner_factory=None
    )
    report = bundle.report

    assert member_names(bundle) == {
        "execution_port": True,
        "runs": True,
        "journal": True,
        "runner": False,
    }
    assert report.runner_blocker == BLOCKER_TURN_RUNNER_UNWIRED
    assert report.blockers == (BLOCKER_TURN_RUNNER_UNWIRED,)
    assert report.consumer_blockers == ()
    assert report.worker_blockers == (BLOCKER_TURN_RUNNER_UNWIRED,)
    assert report.any_wired is True


def test_missing_lease_store_blocks_the_port_that_would_fence_against_it() -> None:
    """A port without a fence store would claim runs nobody can release."""
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(), lease_store_factory=None
    )
    report = bundle.report

    assert bundle.lease_store is None
    assert member_names(bundle)["execution_port"] is False
    assert report.lease_store_blocker == BLOCKER_LEASE_STORE_UNWIRED
    assert report.blockers == (
        BLOCKER_EXECUTION_PORT_UNWIRED,
        BLOCKER_LEASE_STORE_UNWIRED,
    )
    assert report.any_wired is True
    assert report.wired is False


# ---------------------------------------------------------------------------
# The fail-closed default
# ---------------------------------------------------------------------------


def test_everything_declared_unavailable_is_the_empty_bundle() -> None:
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(),
        control_plane=None,
        lease_store_factory=None,
        runs_factory=None,
        journal_factory=None,
        runner_factory=None,
    )
    report = bundle.report

    assert member_names(bundle) == {
        "execution_port": False,
        "runs": False,
        "journal": False,
        "runner": False,
    }
    assert bundle.lease_store is None
    assert report.any_wired is False
    assert report.wired is False
    # Every member is named, in the module's fixed order.
    assert report.blockers == (
        BLOCKER_EXECUTION_PORT_UNWIRED,
        BLOCKER_RUN_SOURCE_UNWIRED,
        BLOCKER_JOURNAL_UNWIRED,
        BLOCKER_TURN_RUNNER_UNWIRED,
        BLOCKER_LEASE_STORE_UNWIRED,
    )
    assert report.consumer_blockers == (BLOCKER_EXECUTION_PORT_UNWIRED,)
    assert report.worker_blockers == (
        BLOCKER_EXECUTION_PORT_UNWIRED,
        BLOCKER_RUN_SOURCE_UNWIRED,
        BLOCKER_JOURNAL_UNWIRED,
        BLOCKER_TURN_RUNNER_UNWIRED,
    )


async def test_empty_bundle_makes_the_host_idle_without_opening_a_session() -> None:
    """The host's own rule, end to end: ``any_wired is False`` → one unwired code."""
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(),
        control_plane=None,
        lease_store_factory=None,
        runs_factory=None,
        journal_factory=None,
        runner_factory=None,
    )
    factory = FakeSessionFactory()
    resource = AgentRunWorkerResource(
        config=ENABLED,
        dependencies=bundle.dependencies,
        session_factory=factory,
    )

    worker_round = await resource.run_worker_round_once()
    consumer_round = await resource.drain_consumer_once()

    assert worker_round["skipped"] is True
    assert worker_round["reason"] == BLOCKER_DEPENDENCIES_UNWIRED
    assert consumer_round["skipped"] is True
    assert consumer_round["reason"] == BLOCKER_DEPENDENCIES_UNWIRED
    assert factory.opened == 0, "a blocked round must not open a session"
    # Nothing was claimed, written or executed, which is the point of idling.
    assert worker_round["claimed"] == 0
    assert consumer_round["claimed"] == 0


async def test_partial_bundle_makes_only_the_incomplete_loop_idle() -> None:
    """A missing runner blocks the worker; the consumer still has its port."""
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(), runner_factory=None
    )
    factory = FakeSessionFactory()
    resource = AgentRunWorkerResource(
        config=ENABLED,
        dependencies=bundle.dependencies,
        session_factory=factory,
    )

    worker_round = await resource.run_worker_round_once()

    assert worker_round["skipped"] is True
    assert worker_round["reason"] == BLOCKER_TURN_RUNNER_UNWIRED
    assert factory.opened == 0


# ---------------------------------------------------------------------------
# A refusing constructor is a code, not a crash
# ---------------------------------------------------------------------------


def test_a_refusing_constructor_becomes_a_composition_code() -> None:
    """A store that raises must not take the process (or the bundle) down with it."""
    from .acceptance_fixtures import RaisingFactory

    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(),
        journal_factory=RaisingFactory(),
    )
    report = bundle.report

    assert bundle.dependencies.journal is None
    assert report.journal_blocker == BLOCKER_JOURNAL_UNWIRED
    # The failure itself is reported as a code, and the reason is an exception class.
    assert "agent_run_host_composition_failed" in report.composition_blockers
    assert dict(report.reasons)["journal"] == "runtimeerror"


def test_a_journal_that_is_not_a_port_is_refused_rather_than_wrapped() -> None:
    from .acceptance_fixtures import FakeLeaseStore

    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(),
        journal_factory=lambda _factory: FakeLeaseStore(),
    )

    assert bundle.dependencies.journal is None
    assert "agent_run_host_composition_failed" in bundle.report.composition_blockers


def test_a_port_that_cannot_claim_is_refused() -> None:
    """``is not None`` is not enough: a port without ``claim_next`` fails every tick."""
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(),
        execution_factory=lambda _store: object(),
    )
    report = bundle.report

    assert bundle.dependencies.execution_port is None
    assert report.execution_port_blocker == BLOCKER_EXECUTION_PORT_UNWIRED
    assert "agent_run_host_composition_failed" in report.composition_blockers
    # The rest of the bundle is intact: a bad port must not cost the worker its parts.
    assert member_names(bundle) == {
        "execution_port": False,
        "runs": True,
        "journal": True,
        "runner": True,
    }
    assert report.worker_blockers == (BLOCKER_EXECUTION_PORT_UNWIRED,)


def test_a_control_plane_that_is_neither_an_instance_nor_a_factory_is_refused() -> None:
    """The adapter's own constructor refuses; the bundle turns that into a code."""
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(),
        control_plane=object(),
    )

    assert bundle.dependencies.execution_port is None
    assert "agent_run_host_composition_failed" in bundle.report.composition_blockers
    assert dict(bundle.report.reasons)["execution_port"] == "typeerror"


@pytest.mark.parametrize(
    "factory_name",
    ("lease_store_factory", "runs_factory", "journal_factory", "runner_factory"),
)
def test_every_member_seam_accepts_an_injected_factory(factory_name: str) -> None:
    """The seams exist for every member, so a host can substitute any of them."""
    from .acceptance_fixtures import (
        FakeAttemptPort,
        FakeLeaseStore,
        FakeRunSource,
        FakeTurnRunner,
    )

    substitutions = {
        "lease_store_factory": lambda _factory: FakeLeaseStore(),
        "runs_factory": lambda _factory: FakeRunSource(),
        "journal_factory": lambda _factory: FakeAttemptPort(),
        "runner_factory": lambda _factory: FakeTurnRunner(),
    }
    bundle = build_agent_run_bundle(
        session_factory=FakeSessionFactory(), **{factory_name: substitutions[factory_name]}
    )

    # The substituted member is accepted as-is (a journal *port* is wrapped, the
    # rest are used verbatim), so nothing reports a composition failure.
    assert bundle.report.composition_blockers == ()
    member = {
        "lease_store_factory": ("lease_store", bundle.lease_store),
        "runs_factory": ("runs", bundle.dependencies.runs),
        "journal_factory": ("journal", bundle.dependencies.journal),
        "runner_factory": ("runner", bundle.dependencies.runner),
    }[factory_name]
    assert member[1] is not None, member[0]
