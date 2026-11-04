"""Assembly never raises, even when the session factory cannot be resolved at all.

This is the case the whole module is designed around: an API process that starts
before its database is reachable (or with an unusable ``database_url``) must still
boot.  A background loop is not a reason to fail readiness for the product, so the
bundle comes back empty, names the reason, and the host idles — exactly as it does
today with no bundle at all.

The second property here is redaction: composition errors are the one place a DSN
can end up inside an exception text, so the report may carry an exception **class**
and a code, never the message.
"""

from __future__ import annotations

import logging

import pytest

import prodavan.infrastructure.persistence.database as database_module
from prodavan.core.infra.agent_run_bundle import (
    BLOCKER_COMPOSITION_FAILED,
    BLOCKER_LEASE_STORE_UNWIRED,
    BLOCKER_SESSION_FACTORY_UNAVAILABLE,
    build_agent_run_bundle,
    build_agent_run_host_dependencies,
    build_agent_run_host_resource,
    install_agent_run_host_dependencies,
    restore_agent_run_host_dependencies,
)
from prodavan.core.infra.agent_run_worker_resource import (
    BLOCKER_DEPENDENCIES_UNWIRED,
    BLOCKER_EXECUTION_PORT_UNWIRED,
    BLOCKER_JOURNAL_UNWIRED,
    BLOCKER_RUN_SOURCE_UNWIRED,
    BLOCKER_TURN_RUNNER_UNWIRED,
    AgentRunHostSettings,
    AgentRunWorkerResource,
    current_host_dependency_provider,
)

from .acceptance_fixtures import SECRET_MARKER, ExplodingSessionFactory, FakeSessionFactory


@pytest.fixture(autouse=True)
def _clean_seam():
    restore_agent_run_host_dependencies()
    yield
    restore_agent_run_host_dependencies()


def test_unresolvable_process_factory_is_a_named_blocker_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_session_factory()`` failing must produce an empty bundle, not a traceback."""

    def explode() -> None:
        raise RuntimeError(SECRET_MARKER)

    monkeypatch.setattr(database_module, "get_session_factory", explode)

    bundle = build_agent_run_bundle()

    report = bundle.report
    assert bundle.dependencies.any_wired is False
    assert bundle.lease_store is None
    assert bundle.session_factory is None
    assert report.wired is False
    assert BLOCKER_SESSION_FACTORY_UNAVAILABLE in report.composition_blockers
    # Every contract member is named as well, so an operator sees the whole picture.
    assert report.blockers == (
        BLOCKER_EXECUTION_PORT_UNWIRED,
        BLOCKER_RUN_SOURCE_UNWIRED,
        BLOCKER_JOURNAL_UNWIRED,
        BLOCKER_TURN_RUNNER_UNWIRED,
        BLOCKER_LEASE_STORE_UNWIRED,
        BLOCKER_SESSION_FACTORY_UNAVAILABLE,
    )
    assert dict(report.reasons)["session_factory"] == "runtimeerror"


def test_the_exception_message_never_reaches_the_report_or_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A composition message can carry a connection string; only codes may leave."""

    def explode() -> None:
        raise RuntimeError(SECRET_MARKER)

    monkeypatch.setattr(database_module, "get_session_factory", explode)
    caplog.set_level(logging.DEBUG)

    bundle = build_agent_run_bundle()
    bundle.snapshot()
    build_agent_run_host_dependencies()
    install_agent_run_host_dependencies()

    rendered = repr(bundle.snapshot())
    assert SECRET_MARKER not in rendered
    assert "sk-live" not in rendered
    for record in caplog.records:
        assert SECRET_MARKER not in record.getMessage()
        assert not record.exc_info, "a composition failure must not log a traceback"


def test_dependencies_only_helper_also_returns_an_empty_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(database_module, "get_session_factory", ExplodingSessionFactory())

    dependencies = build_agent_run_host_dependencies()

    assert dependencies.any_wired is False
    assert dependencies.consumer_blockers == (BLOCKER_EXECUTION_PORT_UNWIRED,)
    assert dependencies.worker_blockers == (
        BLOCKER_EXECUTION_PORT_UNWIRED,
        BLOCKER_RUN_SOURCE_UNWIRED,
        BLOCKER_JOURNAL_UNWIRED,
        BLOCKER_TURN_RUNNER_UNWIRED,
    )


def test_a_non_callable_session_factory_is_refused_up_front() -> None:
    """Composing members over a non-factory would defer the failure to the first tick."""
    bundle = build_agent_run_bundle(session_factory=object())

    assert bundle.session_factory is None
    assert bundle.dependencies.any_wired is False
    assert BLOCKER_SESSION_FACTORY_UNAVAILABLE in bundle.report.composition_blockers
    assert dict(bundle.report.reasons)["session_factory"] == "not_callable"


def test_an_empty_bundle_can_still_be_installed_and_reported() -> None:
    """Installing nothing is a legitimate, honest state — and it stays fail-closed."""
    bundle = install_agent_run_host_dependencies(session_factory=object())

    assert bundle.wired is False
    assert current_host_dependency_provider()() is bundle.dependencies
    assert current_host_dependency_provider()().any_wired is False


async def test_a_host_built_from_an_empty_bundle_idles_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delivered-API failure mode: no factory, flags on, process must stay healthy."""
    monkeypatch.setattr(database_module, "get_session_factory", ExplodingSessionFactory())
    enabled = AgentRunHostSettings(worker_enabled=True, consumer_enabled=True)

    resource = build_agent_run_host_resource(config=enabled)

    assert isinstance(resource, AgentRunWorkerResource)
    assert resource.enabled is True
    assert await resource.health() is False, "enabled but not started yet"
    worker_round = await resource.run_worker_round_once()
    consumer_round = await resource.drain_consumer_once()

    assert worker_round["skipped"] is True
    assert worker_round["reason"] == BLOCKER_DEPENDENCIES_UNWIRED
    assert consumer_round["skipped"] is True
    assert consumer_round["reason"] == BLOCKER_DEPENDENCIES_UNWIRED


def test_composition_failure_code_is_reserved_for_real_failures() -> None:
    """A clean full bundle must never carry the failure code."""
    bundle = build_agent_run_bundle(session_factory=FakeSessionFactory())

    assert BLOCKER_COMPOSITION_FAILED not in bundle.report.composition_blockers
    assert bundle.report.composition_blockers == ()
