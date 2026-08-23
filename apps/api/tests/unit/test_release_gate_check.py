"""Unit tests for tools/release_gate_check.py (L09)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

# apps/api/tests/unit → prodavan root
ROOT = Path(__file__).resolve().parents[4]
GATE = ROOT / "tools" / "release_gate_check.py"


def _load_gate():
    assert GATE.is_file(), f"missing release gate at {GATE}"
    spec = importlib.util.spec_from_file_location("release_gate_check", GATE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_release_gate_check_passes_on_repo() -> None:
    mod = _load_gate()
    assert mod.main() == 0


def test_as_built_cards_include_l00_and_l06() -> None:
    mod = _load_gate()
    cards = mod._as_built_cards()
    assert cards["L00"][0] >= 8
    assert cards["L06"][1] == "done"
    assert cards["L06"][0] >= 8
    assert cards["L01"][1] == "partial"
