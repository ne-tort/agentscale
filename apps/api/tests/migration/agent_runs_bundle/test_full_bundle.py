"""The complete bundle assembles, and the host sees it as fully wired.

Nothing here opens a database: every composed store keeps its factory and opens a
session per method, so "the full composition exists" and "nothing was executed" are
both assertable at once.  The assertions are deliberately about **types and
contracts** rather than about internals, because the bundle's job is to hand the
host exactly the classes those slices already own — no fourth copy, no stand-in.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from prodavan.application.agent_control_plane.service import DurableAgentControlPlane
from prodavan.application.agent_runs.attempts import ProviderAttemptJournal
from prodavan.application.agent_runs.execution_adapter import (
    ControlPlaneAgentRunExecutionAdapter,
)
from prodavan.application.agent_runs.handler import (
    EXECUTABLE_INTENT_KINDS,
    AgentRunIntentHandler,
    IntentExecutionPipeline,
)
from prodavan.application.agent_runs.worker import SessionAgentTurnRunner
from prodavan.core.infra.agent_run_bundle import (
    DEFAULT_TURN_RUNNER_OWNER,
    MEMBER_ORDER,
    AgentRunBundle,
    agent_run_host_dependencies_installed,
    build_agent_run_bundle,
    build_agent_run_host_dependencies,
    build_agent_run_host_resource,
    build_agent_run_intent_dispatcher,
    build_agent_run_intent_handlers,
    build_agent_run_intent_pipeline,
    consumer_lease_seconds,
    restore_agent_run_host_dependencies,
)
from prodavan.core.infra.agent_run_worker_resource import (
    INTENT_QUEUE_NAME,
    AgentRunHostDependencies,
    AgentRunWorkerResource,
    _consumer_lease_seconds,
    current_host_dependency_provider,
    unavailable_host_dependencies,
)
from prodavan.infrastructure.persistence.agent_control_plane.attempt_journal import (
    SqlAlchemyProviderAttemptJournal,
)
from prodavan.infrastructure.persistence.agent_control_plane.lease_store import (
    SqlAlchemyRunLeaseStore,
)
from prodavan.infrastructure.persistence.agent_control_plane.repository import (
    SqlAlchemyAgentControlPlaneRepository,
)

from .acceptance_fixtures import FakeSessionFactory


@pytest.fixture(autouse=True)
def _clean_seam() -> Iterator[None]:
    """No test may inherit a composition installed by another one."""
    restore_agent_run_host_dependencies()
    yield
    restore_agent_run_host_dependencies()


# ---------------------------------------------------------------------------
# Full composition
# ---------------------------------------------------------------------------


def test_full_bundle_composes_every_member_from_the_real_classes() -> None:
    factory = FakeSessionFactory()

    bundle = build_agent_run_bundle(session_factory=factory)

    dependencies = bundle.dependencies
    assert isinstance(dependencies, AgentRunHostDependencies)
    assert isinstance(dependencies.execution_port, ControlPlaneAgentRunExecutionAdapter)
    assert isinstance(dependencies.runs, SqlAlchemyAgentControlPlaneRepository)
    assert isinstance(dependencies.journal, ProviderAttemptJournal)
    assert isinstance(bundle.lease_store, SqlAlchemyRunLeaseStore)
    assert isinstance(dependencies.runner, SessionAgentTurnRunner)
    assert factory.opened == 0, "assembly must not open a session"


def test_the_journal_member_is_the_application_wrapper_over_the_sql_port() -> None:
    """``AgentRunWorker`` refuses a bare port; the wrapper is what enforces the state machine."""
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())

    journal = bundle.dependencies.journal
    assert isinstance(journal, ProviderAttemptJournal)
    # ``ProviderAttemptJournal`` exposes exactly the three methods the pipeline
    # validates, and the worker's isinstance check is satisfied by construction.
    for name in ("begin", "finish", "unresolved_for_run"):
        assert callable(getattr(journal, name, None))


def test_full_bundle_reports_no_blockers_and_is_wired() -> None:
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())
    report = bundle.report

    assert report.blockers == ()
    assert report.wired is True
    assert report.any_wired is True
    assert report.worker_blockers == ()
    assert report.consumer_blockers == ()
    assert bundle.wired is True
    assert bundle.any_wired is True
    # The lease store is not a contract member but it is reported, and present.
    assert report.lease_store_blocker is None
    assert report.composition_blockers == ()


def test_snapshot_is_a_plain_mapping_in_a_fixed_member_order() -> None:
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())
    snapshot = bundle.snapshot()

    assert snapshot["queue_name"] == INTENT_QUEUE_NAME
    assert snapshot["wired"] is True
    assert list(snapshot["members"]) == list(MEMBER_ORDER)
    assert snapshot["blockers"] == []
    assert snapshot["worker_blockers"] == []
    assert snapshot["consumer_blockers"] == []
    # Nothing in the snapshot is a live object a caller could mutate or call.
    assert snapshot["members"] == {
        "execution_port": None,
        "runs": None,
        "journal": None,
        "runner": None,
        "lease_store": None,
    }


def test_dependencies_only_helper_matches_the_bundle() -> None:
    factory = FakeSessionFactory()

    dependencies = build_agent_run_host_dependencies(session_factory=factory)
    bundle = build_agent_run_bundle(session_factory=factory)

    assert isinstance(dependencies, AgentRunHostDependencies)
    assert dependencies.any_wired is True
    assert dependencies.worker_blockers == ()
    assert dependencies.consumer_blockers == ()
    # The helper is the bundle's own contract view, not a second assembly rule.
    assert (bundle.dependencies.any_wired, bundle.dependencies.worker_blockers) == (
        dependencies.any_wired,
        dependencies.worker_blockers,
    )


def test_execution_port_advertises_that_it_is_really_wired() -> None:
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())
    port = bundle.dependencies.execution_port

    assert port.is_wired is True
    assert port.wiring_blocker is None
    # The adapter deliberately has no addressable claim; that is why the intent
    # pipeline must go through ``IntentAddressedLeaseStore`` rather than
    # ``build_execution_handlers`` (see the dispatcher test below).
    assert not hasattr(port, "claim_run")


def test_the_control_plane_factory_is_tenant_bound() -> None:
    """The adapter resolves one control plane per tenant, so the bundle hands it a factory."""
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())
    control_plane = bundle.dependencies.execution_port._control_plane_for("tenant-1")

    assert isinstance(control_plane, DurableAgentControlPlane)
    assert control_plane.is_wired is True


# ---------------------------------------------------------------------------
# The addressed intent pipeline (same parts, different fence)
# ---------------------------------------------------------------------------


def test_intent_pipeline_is_wired_from_the_bundle() -> None:
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())

    pipeline = build_agent_run_intent_pipeline(bundle)

    assert isinstance(pipeline, IntentExecutionPipeline)
    assert pipeline.wired is True
    assert pipeline.blockers == ()
    assert pipeline.lease_store is bundle.lease_store
    assert pipeline.runs is bundle.dependencies.runs
    assert pipeline.journal is bundle.dependencies.journal
    assert pipeline.runner is bundle.dependencies.runner
    assert pipeline.execution_factory is bundle.execution_factory


def test_intent_pipeline_factory_builds_a_port_over_the_given_store() -> None:
    """Every dispatch needs its port over a *narrowed* store, so the factory is the seam."""
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())

    narrowed_store = object()
    port = bundle.execution_factory(narrowed_store)

    assert isinstance(port, ControlPlaneAgentRunExecutionAdapter)
    assert port.is_wired is True


def test_registry_executes_the_created_intent() -> None:
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())

    registry = build_agent_run_intent_handlers(bundle)

    assert set(registry) == set(EXECUTABLE_INTENT_KINDS)
    handler = registry[next(iter(EXECUTABLE_INTENT_KINDS))]
    assert isinstance(handler, AgentRunIntentHandler)
    assert handler.pipeline is not None
    assert handler.pipeline.wired is True
    assert handler.blockers == ()


def test_registry_can_park_non_executable_kinds_terminally() -> None:
    """Kinds nothing will project must not retry for a day; the caller names them."""
    from prodavan.application.agent_runs.dispatcher import IntentKind

    kind = next(kind for kind in IntentKind if kind not in EXECUTABLE_INTENT_KINDS)
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())

    registry = build_agent_run_intent_handlers(bundle, terminal_kinds=(kind,))

    assert kind in registry
    assert set(EXECUTABLE_INTENT_KINDS) <= set(registry)


def test_registry_refuses_to_park_an_executable_kind() -> None:
    """Guessing what a caller meant is the opposite of fail-closed."""
    kind = next(iter(EXECUTABLE_INTENT_KINDS))
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())

    with pytest.raises(ValueError):
        build_agent_run_intent_handlers(bundle, terminal_kinds=(kind,))


def test_dispatcher_uses_the_addressed_registry_not_the_claim_run_registry() -> None:
    """``build_execution_handlers`` needs ``port.claim_run``; the fenced adapter has none.

    Composing the consumer loop with that registry is what would walk every intent
    through ``intent_handler_not_wired`` retries into the poison-pill refusal, so the
    bundle's dispatcher is asserted to carry the addressed handler instead.
    """
    from prodavan.application.agent_runs.dispatcher import EnqueueIntentDispatcher

    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())

    dispatcher, consumer = build_agent_run_intent_dispatcher(bundle)

    assert isinstance(dispatcher, EnqueueIntentDispatcher)
    assert dispatcher.registered_kinds == EXECUTABLE_INTENT_KINDS
    assert dispatcher.registered_kinds, "an empty registry would retry every intent"
    assert consumer.queue_name == INTENT_QUEUE_NAME


def test_consumer_lease_seconds_is_delegated_not_re_derived() -> None:
    """Two copies of the lease formula would drift into quarantining live intents."""
    for batch in (1, 5, 25, 200, 10**6):
        assert consumer_lease_seconds(batch) == _consumer_lease_seconds(int(batch))


# ---------------------------------------------------------------------------
# Host resource assembly
# ---------------------------------------------------------------------------


def test_host_resource_is_built_with_bundle_dispatcher_and_factory() -> None:
    restore_agent_run_host_dependencies()
    factory = FakeSessionFactory()

    resource = build_agent_run_host_resource(session_factory=factory)

    assert isinstance(resource, AgentRunWorkerResource)
    assert resource.name == "agent_run_host"
    assert resource.report()["queue_name"] == INTENT_QUEUE_NAME
    assert resource.enabled is False, "the loops stay opt-in through their own flags"
    # Installing is part of the composition entry point unless it is switched off.
    assert agent_run_host_dependencies_installed() is True
    restore_agent_run_host_dependencies()


def test_host_resource_can_skip_installation_and_stay_unwired() -> None:
    restore_agent_run_host_dependencies()
    factory = FakeSessionFactory()

    build_agent_run_host_resource(session_factory=factory, install=False)

    assert agent_run_host_dependencies_installed() is False
    assert current_host_dependency_provider() is unavailable_host_dependencies
