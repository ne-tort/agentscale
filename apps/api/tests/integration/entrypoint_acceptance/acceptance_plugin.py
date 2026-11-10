"""Early pytest isolation for the two-entrypoint real acceptance harness.

Load with -p tests.integration.entrypoint_acceptance.acceptance_plugin and pass
--confcutdir=tests/integration/entrypoint_acceptance. The hook runs before the
parent tests/conftest.py can import prodavan.main or execute PostgreSQL/live
API/kubectl probes. Local fixtures remain enabled and mandatory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

ACCEPTANCE_DIR = Path(__file__).resolve().parent
REQUIRED_CONFCUTDIR_ARG = "--confcutdir=tests/integration/entrypoint_acceptance"
PLUGIN_PATH = "tests.integration.entrypoint_acceptance.acceptance_plugin"
EARLY_GUARD_ATTRIBUTE = "_entrypoint_acceptance_early_isolation_guard"


def _resolved_cutoff(config: Any) -> Any:
    """Return the confcutdir pytest will actually apply for this invocation.

    ``pytest_load_initial_conftests`` runs before the normal argument pass, so
    ``config.option.confcutdir`` is still unset there even when the flag was
    supplied. Parsing the explicit args with pytest's own parser (the documented
    hook contract) yields exactly the value pytest later applies, so the required
    invocation is validated instead of being rejected as a bare one.
    """
    parsed = getattr(config, "_entrypoint_acceptance_parsed_args", None)
    if parsed is not None:
        return getattr(parsed, "confcutdir", None)
    return getattr(config.option, "confcutdir", None)


def validate_acceptance_invocation(config: Any) -> None:
    option = config.option
    if getattr(option, "noconftest", False):
        raise pytest.UsageError(
            "--noconftest is forbidden: local entrypoint fixtures/gates are required"
        )
    raw_cutoff = _resolved_cutoff(config)
    if raw_cutoff is None or not str(raw_cutoff).strip():
        raise pytest.UsageError(
            f"entrypoint acceptance requires {REQUIRED_CONFCUTDIR_ARG}; "
            "a bare pytest invocation is unsupported"
        )
    try:
        actual = Path(str(raw_cutoff)).expanduser().resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise pytest.UsageError("entrypoint acceptance confcutdir is invalid") from exc
    if actual != ACCEPTANCE_DIR:
        raise pytest.UsageError(
            f"entrypoint acceptance requires exactly {REQUIRED_CONFCUTDIR_ARG}"
        )
    if not getattr(config, EARLY_GUARD_ATTRIBUTE, False):
        raise pytest.UsageError(f"Load the early isolation guard with -p {PLUGIN_PATH}")


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: Any, parser: Any, args: list[str]) -> None:
    setattr(early_config, EARLY_GUARD_ATTRIBUTE, True)
    # pytest has not applied the normal argument pass yet at this hook point.
    parsed = parser.parse_known_args(args=list(args or ()), namespace=None)
    setattr(early_config, "_entrypoint_acceptance_parsed_args", parsed)
    validate_acceptance_invocation(early_config)


def pytest_configure(config: Any) -> None:
    validate_acceptance_invocation(config)


__all__ = [
    "ACCEPTANCE_DIR",
    "EARLY_GUARD_ATTRIBUTE",
    "PLUGIN_PATH",
    "REQUIRED_CONFCUTDIR_ARG",
    "pytest_configure",
    "pytest_load_initial_conftests",
    "validate_acceptance_invocation",
]
