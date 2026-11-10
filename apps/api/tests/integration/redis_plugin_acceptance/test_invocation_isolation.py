"""Static/runtime collection-isolation checks; no real service fixture required."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from .invocation_guard import (
    ACCEPTANCE_DIR,
    REQUIRED_CONFCUTDIR_ARG,
    validate_acceptance_invocation,
)

pytestmark = [pytest.mark.integration, pytest.mark.redis_acceptance]

_TESTS_ROOT = ACCEPTANCE_DIR.parent.parent
_PARENT_CONFTEST = (_TESTS_ROOT / "conftest.py").resolve()
_TARGET = "tests/integration/redis_plugin_acceptance"
_FORBIDDEN_PARENT_IMPORTS = frozenset({"prodavan.config.settings", "prodavan.main"})


def _config(*, confcutdir: object, noconftest: bool = False) -> Any:
    return SimpleNamespace(
        option=SimpleNamespace(confcutdir=confcutdir, noconftest=noconftest)
    )


def test_readme_commands_pin_exact_confcutdir_without_early_plugin_claim() -> None:
    lines = (ACCEPTANCE_DIR / "README.md").read_text(encoding="utf-8").splitlines()
    commands = [
        line.strip() for line in lines if line.strip().startswith("python -m pytest ")
    ]
    assert len(commands) >= 2
    for command in commands:
        assert _TARGET in command
        assert REQUIRED_CONFCUTDIR_ARG in command
        assert "invocation_guard" not in command
        assert "--noconftest" not in command


def test_local_validation_rejects_missing_wrong_or_disabled_isolation() -> None:
    # These are local validation semantics, not a claim that a bare invocation can
    # be rejected before its parent conftest has already been imported.
    unsafe = (
        _config(confcutdir=None),
        _config(confcutdir=""),
        _config(confcutdir=_TESTS_ROOT),
        _config(confcutdir=ACCEPTANCE_DIR.parent),
        _config(confcutdir=ACCEPTANCE_DIR, noconftest=True),
    )
    for config in unsafe:
        with pytest.raises(pytest.UsageError):
            validate_acceptance_invocation(config)

    validate_acceptance_invocation(_config(confcutdir=ACCEPTANCE_DIR))


def test_no_intermediate_conftest_exists_between_tests_root_and_harness() -> None:
    # The hazardous parent exists, but exact confcutdir excludes it. Only the
    # local harness conftest is allowed inside the cutoff path.
    assert _PARENT_CONFTEST.is_file()
    relative = ACCEPTANCE_DIR.relative_to(_TESTS_ROOT)
    cursor = _TESTS_ROOT
    for part in relative.parts[:-1]:
        cursor = cursor / part
        assert not (cursor / "conftest.py").exists(), (
            "an intermediate conftest could execute inside the acceptance cutoff"
        )
    assert (ACCEPTANCE_DIR / "conftest.py").is_file()


def test_exact_cutoff_without_early_plugin_excludes_parent_application_imports(
    request: pytest.FixtureRequest,
) -> None:
    config = request.config
    actual = Path(str(config.option.confcutdir)).expanduser().resolve()
    assert actual == ACCEPTANCE_DIR
    assert config.option.noconftest is False
    assert _FORBIDDEN_PARENT_IMPORTS.isdisjoint(sys.modules), (
        "parent application settings/main were imported before acceptance isolation"
    )

    for module in tuple(sys.modules.values()):
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            loaded = Path(module_file).resolve()
        except (OSError, RuntimeError):
            continue
        assert loaded != _PARENT_CONFTEST, "parent tests/conftest.py was imported"
