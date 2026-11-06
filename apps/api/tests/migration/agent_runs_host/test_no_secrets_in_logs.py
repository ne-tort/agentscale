"""No secrets, prompts or payloads in logs or in the health report (MIG-AR-HOST-01).

A background host is a logging hotspot: every tick can log, every failure can
log a stack, and every operator reads the health report.  Three classes of value
must never appear on those paths, and the tests below assert each one strictly:

* a credential (a provider key, a connection string with a password);
* a prompt or a run payload (customer content);
* a raw exception message — it can contain either of the above, plus an
  identifier tuple nobody should find in a log aggregator.

The host solves this by construction: ticks return a closed counter vocabulary
filtered through ``SAFE_RESULT_KEYS``, failures log ``type(exc).__qualname__``
only, and every reason is a module-level constant.  The tests check the
construction *and* the outcome, so a future "just log the exception" change fails
loudly instead of leaking quietly.
"""

from __future__ import annotations

import inspect
import logging

import pytest

from prodavan.core.infra import agent_run_worker_resource as host
from prodavan.core.infra.agent_run_worker_resource import (
    LOOP_CONSUMER,
    LOOP_WORKER,
    SAFE_RESULT_KEYS,
    AgentRunWorkerResource,
    current_host_dependency_provider,
    register_host_dependency_provider,
)

from .acceptance_fixtures import (
    PROMPT_MARKER,
    SECRET_MARKER,
    FakeDispatcher,
    FakeLock,
    FakeSessionFactory,
    FakeWorker,
    enabled_config,
    wait_until,
    wired_dependencies,
)

#: Every forbidden marker the fakes deliberately carry.
FORBIDDEN = (SECRET_MARKER, PROMPT_MARKER, "postgresql+asyncpg://", "Bearer ")


@pytest.fixture(autouse=True)
def _restore_provider() -> None:
    previous = current_host_dependency_provider()
    yield
    register_host_dependency_provider(previous)


def _assert_clean(text: str) -> None:
    for marker in FORBIDDEN:
        assert marker not in text, f"{marker!r} leaked into: {text!r}"


@pytest.mark.asyncio
async def test_failing_tick_logs_the_class_and_withholds_the_message(caplog) -> None:
    """The exception message carries a secret; only its class may be logged."""
    dispatcher = FakeDispatcher(
        error=RuntimeError(
            f"connection refused dsn=postgresql+asyncpg://app:{SECRET_MARKER}@db/x "
            f"prompt={PROMPT_MARKER}"
        ),
        error_times=1,
    )
    resource = AgentRunWorkerResource(
        config=enabled_config(worker=False, consumer=True, consumer_interval_sec=3600),
        dependencies=wired_dependencies(),
        dispatcher=dispatcher,
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    with caplog.at_level(logging.DEBUG, logger="prodavan"):
        await resource.startup()
        assert await wait_until(
            lambda: resource.report()["loops"][LOOP_CONSUMER]["errors"] >= 1, timeout=3.0
        )
        await resource.shutdown()

    messages = [record.getMessage() for record in caplog.records]
    assert any("tick failed" in message for message in messages)
    for message in messages:
        _assert_clean(message)
    assert any("RuntimeError" in message for message in messages)
    # And explicitly: the raised message itself never reached a record.
    for record in caplog.records:
        _assert_clean(str(record.exc_info))


@pytest.mark.asyncio
async def test_report_and_health_never_carry_a_tick_payload(caplog) -> None:
    """A tick that throws back a rich dict must be filtered to counters only."""
    rich_result = {
        "skipped": False,
        "reason": "ok",
        "claimed": 1,
        "delivered": 1,
        "run_id": "run-42",
        "prompt": PROMPT_MARKER,
        "authorization": f"Bearer {SECRET_MARKER}",
        "dsn": f"postgresql+asyncpg://app:{SECRET_MARKER}@db/x",
        "payload": {"text": PROMPT_MARKER},
    }

    class RefusingDispatcher:
        async def dispatch_batch(self, *, owner_id, limit, repair_expired=0):
            raise AssertionError("the injected dispatcher is never entered here")

    resource = AgentRunWorkerResource(
        config=enabled_config(worker=False, consumer=True),
        dependencies=wired_dependencies(),
        dispatcher=RefusingDispatcher(),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    assert set(rich_result) - SAFE_RESULT_KEYS  # the fixture really is out of vocabulary
    redacted = host._redacted(rich_result)

    assert set(redacted) <= SAFE_RESULT_KEYS
    assert redacted == {"skipped": False, "reason": "ok", "claimed": 1, "delivered": 1}
    _assert_clean(repr(resource.report()))


@pytest.mark.asyncio
async def test_successful_tick_logs_counters_only(caplog) -> None:
    resource = AgentRunWorkerResource(
        config=enabled_config(worker=True, consumer=False, worker_interval_sec=3600),
        dependencies=wired_dependencies(),
        run_worker=FakeWorker(),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    with caplog.at_level(logging.DEBUG, logger="prodavan"):
        await resource.startup()
        assert await wait_until(
            lambda: resource.report()["loops"][LOOP_WORKER]["ticks"] >= 1, timeout=3.0
        )
        await resource.shutdown()

    for record in caplog.records:
        _assert_clean(record.getMessage())
    _assert_clean(repr(resource.report()))


def test_no_log_call_formats_an_exception_message() -> None:
    """Structural guard: ``logger.*`` may name the exception only by class.

    ``logger.exception(...)`` writes a traceback whose final line is the
    exception message, and ``%s``/``%r`` of a caught exception does the same.  The
    host therefore may not use either; it logs ``type(exc).__qualname__``.
    """
    source = inspect.getsource(host)
    assert "logger.exception(" not in source
    assert "%s\", exc" not in source
    assert "type(exc).__qualname__" in source


def test_settings_surface_holds_no_endpoint_or_secret_field() -> None:
    """The additive settings block is six knobs: no URL, no key, no endpoint."""
    names = [
        field
        for field in host.AgentRunHostSettings.__dataclass_fields__
        if not field.startswith("_")
    ]
    assert names == [
        "worker_enabled",
        "worker_interval_sec",
        "worker_batch_max",
        "consumer_enabled",
        "consumer_interval_sec",
        "consumer_batch_max",
    ]
    for name in names:
        for forbidden in ("url", "token", "secret", "password", "key", "dsn"):
            assert forbidden not in name


def test_reason_codes_are_constants_not_free_text() -> None:
    """Every reason the loops publish is a module constant (a closed vocabulary)."""
    constants = {
        value
        for name, value in vars(host).items()
        if name.startswith("REASON_") or name.startswith("BLOCKER_")
    }
    for value in constants:
        assert isinstance(value, str)
        assert value == value.strip()
        assert " " not in value


@pytest.mark.asyncio
async def test_blocker_log_line_is_a_code_only(caplog) -> None:
    register_host_dependency_provider(host.unavailable_host_dependencies)
    resource = AgentRunWorkerResource(
        config=enabled_config(worker_interval_sec=3600, consumer_interval_sec=3600),
        session_factory=FakeSessionFactory(),
        lock=FakeLock(),
    )
    with caplog.at_level(logging.WARNING, logger="prodavan"):
        await resource.startup()
        await resource.shutdown()
    messages = [record.getMessage() for record in caplog.records]
    assert any(host.BLOCKER_DEPENDENCIES_UNWIRED in message for message in messages)
    for message in messages:
        _assert_clean(message)
