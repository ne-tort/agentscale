"""Acceptance-only pytest enforcement and parent-conftest isolation.

The supported invocation loads this module with -p and passes exactly:
    --confcutdir=tests/integration/kafka_plugin_acceptance

The early load hook rejects a missing/wrong cutoff or --noconftest before
pytest can import apps/api/tests/conftest.py and execute its import-time probes.
Local conftest fixtures remain mandatory. KAFKA_PLUGIN_ACCEPTANCE_REQUIRED=1
also requires collected>0 and skipped=0; REQUIRE_COMPLETE=1 rejects pending.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .config import acceptance_required, require_complete

ACCEPTANCE_DIR = Path(__file__).resolve().parent
REQUIRED_CONFCUTDIR_ARG = "--confcutdir=tests/integration/kafka_plugin_acceptance"
EARLY_GUARD_ATTRIBUTE = "_kafka_acceptance_early_isolation_guard"


def validate_acceptance_invocation(config: Any) -> None:
    """Reject any invocation that can import parent or disable local conftest."""

    # During pytest_load_initial_conftests parsed values live in
    # known_args_namespace; Config.option is populated only by the full parse.
    option = getattr(config, "known_args_namespace", None) or config.option
    if getattr(option, "noconftest", False):
        raise pytest.UsageError(
            "--noconftest is forbidden: local acceptance fixtures/gates are required"
        )
    raw_cutoff = getattr(option, "confcutdir", None)
    if raw_cutoff is None or not str(raw_cutoff).strip():
        raise pytest.UsageError(
            f"Kafka acceptance requires {REQUIRED_CONFCUTDIR_ARG}; "
            "a bare pytest invocation is unsupported"
        )
    try:
        actual = Path(str(raw_cutoff)).expanduser().resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise pytest.UsageError("Kafka acceptance confcutdir is invalid") from exc
    if actual != ACCEPTANCE_DIR:
        raise pytest.UsageError(
            f"Kafka acceptance requires exactly {REQUIRED_CONFCUTDIR_ARG}"
        )
    if not getattr(config, EARLY_GUARD_ATTRIBUTE, False):
        raise pytest.UsageError(
            "Load the early isolation guard with "
            "-p tests.integration.kafka_plugin_acceptance.acceptance_plugin"
        )


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: Any) -> None:
    # This -p plugin is registered before pytest imports initial conftest files.
    # Set the marker only for this hook: a local conftest loaded without -p
    # cannot counterfeit completion of the early isolation check.
    setattr(early_config, EARLY_GUARD_ATTRIBUTE, True)
    validate_acceptance_invocation(early_config)
    return None


def pytest_configure(config: Any) -> None:
    validate_acceptance_invocation(config)
    if getattr(config, "_kafka_acceptance_markers_registered", False):
        return
    config._kafka_acceptance_markers_registered = True
    config.addinivalue_line("markers", "kafka_plugin_acceptance: real-service plugin event acceptance")
    config.addinivalue_line("markers", "pending_capability: intentionally pending production capability")


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if not acceptance_required():
        return
    if getattr(session.config, "_kafka_acceptance_contract_enforced", False):
        return
    session.config._kafka_acceptance_contract_enforced = True
    collected = int(getattr(session, "testscollected", 0) or 0)
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    stats = getattr(reporter, "stats", {}) if reporter is not None else {}
    skipped = len(stats.get("skipped", []))
    xfailed = len(stats.get("xfailed", []))

    failure_reasons: list[str] = []
    if collected == 0:
        failure_reasons.append("acceptance run collected no tests")
    if skipped:
        failure_reasons.append(f"acceptance run skipped {skipped} collection item(s)")
    if require_complete() and xfailed:
        failure_reasons.append(
            f"complete acceptance mode found {xfailed} explicit pending item(s)"
        )
    if failure_reasons and exitstatus == pytest.ExitCode.OK:
        terminal = reporter
        if terminal is not None:
            terminal.write_sep("=", "ACCEPTANCE CONTRACT FAILED")
            for reason in failure_reasons:
                terminal.write_line(reason)
        session.exitstatus = 1


__all__ = [
    "ACCEPTANCE_DIR",
    "REQUIRED_CONFCUTDIR_ARG",
    "pytest_configure",
    "pytest_load_initial_conftests",
    "pytest_sessionfinish",
    "validate_acceptance_invocation",
]
