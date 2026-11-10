"""Static invocation-isolation checks; no real service fixture is required.

These checks are written, not executed, in this change.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from .acceptance_plugin import (
    ACCEPTANCE_DIR,
    EARLY_GUARD_ATTRIBUTE,
    validate_acceptance_invocation,
)

pytestmark = [pytest.mark.integration, pytest.mark.kafka_plugin_acceptance]

_TESTS_ROOT = ACCEPTANCE_DIR.parent.parent


def test_active_invocation_has_exact_cutoff_local_conftest_and_early_guard(
    request: pytest.FixtureRequest,
) -> None:
    config = request.config
    actual_cutoff = Path(str(config.option.confcutdir)).expanduser().resolve()
    assert actual_cutoff == ACCEPTANCE_DIR
    assert config.option.noconftest is False
    assert getattr(config, EARLY_GUARD_ATTRIBUTE, False) is True
    assert (ACCEPTANCE_DIR / "conftest.py").is_file()
    assert config._kafka_acceptance_markers_registered is True


def test_no_intermediate_conftest_exists_between_tests_root_and_harness() -> None:
    # The hazardous parent exists, but confcutdir excludes it. Only the local
    # harness conftest is allowed inside the cutoff path.
    assert (_TESTS_ROOT / "conftest.py").is_file()
    relative = ACCEPTANCE_DIR.relative_to(_TESTS_ROOT)
    cursor = _TESTS_ROOT
    for part in relative.parts[:-1]:
        cursor = cursor / part
        assert not (cursor / "conftest.py").exists(), (
            "an intermediate conftest could execute inside the acceptance cutoff"
        )
    assert not (ACCEPTANCE_DIR.parent / "conftest.py").exists()
    assert (ACCEPTANCE_DIR / "conftest.py").is_file()


def test_invocation_guard_rejects_missing_wrong_or_disabled_isolation() -> None:
    unsafe_options = (
        SimpleNamespace(confcutdir=None, noconftest=False),
        SimpleNamespace(confcutdir="", noconftest=False),
        SimpleNamespace(confcutdir=str(_TESTS_ROOT), noconftest=False),
        SimpleNamespace(confcutdir=str(ACCEPTANCE_DIR), noconftest=True),
    )
    for option in unsafe_options:
        config = SimpleNamespace(option=option)
        setattr(config, EARLY_GUARD_ATTRIBUTE, True)
        with pytest.raises(pytest.UsageError):
            validate_acceptance_invocation(config)

    without_early_plugin = SimpleNamespace(
        option=SimpleNamespace(confcutdir=str(ACCEPTANCE_DIR), noconftest=False)
    )
    setattr(without_early_plugin, EARLY_GUARD_ATTRIBUTE, False)
    with pytest.raises(pytest.UsageError):
        validate_acceptance_invocation(without_early_plugin)
