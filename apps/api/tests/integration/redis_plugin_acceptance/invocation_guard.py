"""Local Redis acceptance invocation validation, not an early bare-run guard.

With the supported exact cutoff, pytest loads only this harness conftest and does
not load apps/api/tests/conftest.py.  A missing/wrong cutoff is unsupported: by
the time this local validator runs, parent conftest may already have imported
application settings/main and run their probes.  The validator therefore enforces
the supported shape for documented invocations; it cannot undo or make early a
bare invocation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

ACCEPTANCE_DIR = Path(__file__).resolve().parent
REQUIRED_CONFCUTDIR_ARG = "--confcutdir=tests/integration/redis_plugin_acceptance"


def validate_acceptance_invocation(config: Any) -> None:
    """Enforce the local fixture boundary when this conftest is active."""

    option = config.option
    if getattr(option, "noconftest", False):
        raise pytest.UsageError(
            "--noconftest is unsupported: local Redis fixtures/no-skip gates are required"
        )
    raw_cutoff = getattr(option, "confcutdir", None)
    if raw_cutoff is None or not str(raw_cutoff).strip():
        raise pytest.UsageError(
            f"Redis acceptance requires {REQUIRED_CONFCUTDIR_ARG}; "
            "a bare/no-cutoff invocation is unsupported"
        )
    try:
        actual = Path(str(raw_cutoff)).expanduser().resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise pytest.UsageError("Redis acceptance confcutdir is invalid") from exc
    if actual != ACCEPTANCE_DIR:
        raise pytest.UsageError(
            f"Redis acceptance requires exactly {REQUIRED_CONFCUTDIR_ARG}"
        )


__all__ = [
    "ACCEPTANCE_DIR",
    "REQUIRED_CONFCUTDIR_ARG",
    "validate_acceptance_invocation",
]
