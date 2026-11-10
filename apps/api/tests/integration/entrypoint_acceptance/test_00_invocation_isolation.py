"""Static early-invocation isolation checks; no service fixture is required."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from .acceptance_plugin import (
    ACCEPTANCE_DIR,
    EARLY_GUARD_ATTRIBUTE,
    PLUGIN_PATH,
    REQUIRED_CONFCUTDIR_ARG,
    validate_acceptance_invocation,
)

pytestmark = [pytest.mark.integration, pytest.mark.entrypoint_acceptance]
_TESTS_ROOT = ACCEPTANCE_DIR.parent.parent
_EXACT_COMMAND = (
    "python -m pytest -p tests.integration.entrypoint_acceptance.acceptance_plugin "
    "--confcutdir=tests/integration/entrypoint_acceptance "
    "tests/integration/entrypoint_acceptance"
)


def test_active_invocation_has_early_guard_cutoff_and_local_conftest(
    request: pytest.FixtureRequest,
) -> None:
    config = request.config
    assert Path(str(config.option.confcutdir)).expanduser().resolve() == ACCEPTANCE_DIR
    assert config.option.noconftest is False
    assert getattr(config, EARLY_GUARD_ATTRIBUTE, False) is True
    assert (ACCEPTANCE_DIR / "conftest.py").is_file()


def test_no_intermediate_conftest_can_execute_inside_cutoff() -> None:
    assert (_TESTS_ROOT / "conftest.py").is_file()
    relative = ACCEPTANCE_DIR.relative_to(_TESTS_ROOT)
    cursor = _TESTS_ROOT
    for part in relative.parts[:-1]:
        cursor = cursor / part
        assert not (cursor / "conftest.py").exists()
    assert not (ACCEPTANCE_DIR.parent / "conftest.py").exists()
    assert (ACCEPTANCE_DIR / "conftest.py").is_file()


def test_early_guard_rejects_bare_wrong_disabled_or_unplugged_invocation() -> None:
    unsafe = (
        SimpleNamespace(confcutdir=None, noconftest=False),
        SimpleNamespace(confcutdir="", noconftest=False),
        SimpleNamespace(confcutdir=str(_TESTS_ROOT), noconftest=False),
        SimpleNamespace(confcutdir=str(ACCEPTANCE_DIR), noconftest=True),
    )
    for option in unsafe:
        config = SimpleNamespace(option=option)
        setattr(config, EARLY_GUARD_ATTRIBUTE, True)
        with pytest.raises(pytest.UsageError):
            validate_acceptance_invocation(config)
    without_plugin = SimpleNamespace(
        option=SimpleNamespace(confcutdir=str(ACCEPTANCE_DIR), noconftest=False)
    )
    with pytest.raises(pytest.UsageError):
        validate_acceptance_invocation(without_plugin)


def test_early_hook_resolves_confcutdir_before_pytest_applies_arguments() -> None:
    """pytest_load_initial_conftests runs before the normal argument pass.

    `config.option.confcutdir` is therefore still unset at that point even for
    the documented invocation, so the guard must read the parsed args instead of
    rejecting a valid run as bare. This regressed silently before: the unit-level
    checks above only ever exercised the post-parse config.
    """
    from .acceptance_plugin import pytest_load_initial_conftests

    class _Parser:
        def parse_known_args(self, args: list[str], namespace: object = None) -> object:
            assert "--confcutdir=tests/integration/entrypoint_acceptance" in args
            return SimpleNamespace(confcutdir=str(ACCEPTANCE_DIR), noconftest=False)

    early_config = SimpleNamespace(
        option=SimpleNamespace(confcutdir=None, noconftest=False),
    )
    pytest_load_initial_conftests(
        early_config,
        _Parser(),
        [f"--confcutdir={REQUIRED_CONFCUTDIR_ARG.split("=", 1)[1]}", str(ACCEPTANCE_DIR)],
    )
    assert getattr(early_config, EARLY_GUARD_ATTRIBUTE, False) is True


def test_early_hook_still_rejects_a_bare_invocation_without_the_flag() -> None:
    from .acceptance_plugin import pytest_load_initial_conftests

    class _Parser:
        def parse_known_args(self, args: list[str], namespace: object = None) -> object:
            return SimpleNamespace(confcutdir=None, noconftest=False)

    early_config = SimpleNamespace(
        option=SimpleNamespace(confcutdir=None, noconftest=False),
    )
    with pytest.raises(pytest.UsageError):
        pytest_load_initial_conftests(early_config, _Parser(), [str(ACCEPTANCE_DIR)])


def test_readme_pins_plugin_cutoff_unexecuted_status_and_no_skip_contract() -> None:
    text = (ACCEPTANCE_DIR / "README.md").read_text(encoding="utf-8")
    assert _EXACT_COMMAND in text
    assert REQUIRED_CONFCUTDIR_ARG in text
    assert PLUGIN_PATH in text
    assert "--noconftest" in text
    assert "not executed" in text
    assert "collected > 0" in text and "skipped == 0" in text
