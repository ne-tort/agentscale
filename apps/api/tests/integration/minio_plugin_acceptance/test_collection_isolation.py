"""Static and runtime guards against parent pytest import side effects."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .config import acceptance_requested
from .support import require

pytestmark = pytest.mark.minio_acceptance
_PACKAGE_DIR = Path(__file__).resolve().parent
_PARENT_CONFTEST = (_PACKAGE_DIR.parent.parent / "conftest.py").resolve()
_EXACT_COMMAND = (
    "pytest --confcutdir=tests/integration/minio_plugin_acceptance "
    "tests/integration/minio_plugin_acceptance -q"
)


def test_readme_pins_confcutdir_and_never_disables_local_conftest() -> None:
    text = (_PACKAGE_DIR / "README.md").read_text(encoding="utf-8")
    require(_EXACT_COMMAND in text, "README lost the isolated confcutdir command")
    require("--noconftest" not in text, "README must not disable local fixtures and gates")
    require("tests/conftest.py" in text, "README must document the parent side-effect boundary")


def test_acceptance_collection_did_not_load_parent_conftest(
    pytestconfig: pytest.Config,
) -> None:
    if not acceptance_requested():
        pytest.skip("collection runtime guard is active only for an explicit acceptance run")
    confcutdir = getattr(pytestconfig.option, "confcutdir", None)
    require(confcutdir is not None, "acceptance collection has no confcutdir")
    require(Path(confcutdir).resolve() == _PACKAGE_DIR, "wrong acceptance confcutdir")

    for module in tuple(sys.modules.values()):
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            loaded_path = Path(module_file).resolve()
        except (OSError, RuntimeError):
            continue
        require(loaded_path != _PARENT_CONFTEST, "parent tests/conftest.py was imported")
