#!/usr/bin/env python3
"""Release gate lite — as-built Quality vs checklist-master drift (L09).

Exit 0 when consistent; non-zero on drift or missing workflows.
Stdlib only — safe for CI without installing the API package.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AS_BUILT = ROOT / "docs" / "target" / "12-layer-docs"
CHECKLIST = ROOT / "docs" / "target" / "11-implementation-plan" / "checklist-master.md"
WORKFLOWS = ROOT / ".github" / "workflows"

_LAYER_RE = re.compile(r"^L(\d{2})")
_QUALITY_RE = re.compile(r"^\|\s*Quality\s*\|\s*(\d+)\s*\|", re.MULTILINE)
_STATUS_RE = re.compile(r"^\|\s*Status\s*\|\s*([^\|]+?)\s*\|", re.MULTILINE)
_CHECKLIST_ROW_RE = re.compile(
    r"\|\s*\[(L\d{2})\][^|]*\|\s*(\w+)\s*\|\s*(\d+)\s*\|",
    re.MULTILINE,
)


def _as_built_cards() -> dict[str, tuple[int, str]]:
    """layer → (quality, status)."""
    out: dict[str, tuple[int, str]] = {}
    for path in sorted(AS_BUILT.glob("L*.md")):
        m = _LAYER_RE.match(path.stem)
        if not m:
            continue
        layer = f"L{m.group(1)}"
        text = path.read_text(encoding="utf-8", errors="replace")
        qm = _QUALITY_RE.search(text)
        sm = _STATUS_RE.search(text)
        if qm:
            status = (sm.group(1).strip() if sm else "").lower()
            out[layer] = (int(qm.group(1)), status)
    return out


def _checklist_rows() -> dict[str, tuple[str, int]]:
    text = CHECKLIST.read_text(encoding="utf-8", errors="replace")
    out: dict[str, tuple[str, int]] = {}
    for m in _CHECKLIST_ROW_RE.finditer(text):
        out[m.group(1)] = (m.group(2), int(m.group(3)))
    return out


def main() -> int:
    errors: list[str] = []
    as_built = _as_built_cards()
    checklist = _checklist_rows()

    if not as_built:
        errors.append("no as-built Quality scores found")
    if not checklist:
        errors.append("no checklist-master layer rows found")

    for layer, (quality, status) in sorted(as_built.items()):
        if layer not in checklist:
            errors.append(f"{layer}: in as-built (Q={quality}) but missing from checklist-master")
            continue
        c_status, cq = checklist[layer]
        if cq != quality:
            errors.append(
                f"{layer}: checklist Quality={cq} drifts from as-built Quality={quality} (status={c_status})"
            )
        if status == "done" and quality < 8:
            errors.append(f"{layer}: Status=done requires Quality >= 8 (got {quality})")

    for layer, (status, cq) in sorted(checklist.items()):
        if layer not in as_built:
            errors.append(f"{layer}: in checklist-master but no as-built card Quality")

    required_workflows = ("ci-api.yml", "ci-nightly.yml")
    for name in required_workflows:
        if not (WORKFLOWS / name).is_file():
            errors.append(f"missing workflow: .github/workflows/{name}")

    veto_text = CHECKLIST.read_text(encoding="utf-8", errors="replace").lower()
    for ban in ("password-login", "cli_subscription", "openclaw"):
        if ban not in veto_text:
            errors.append(f"checklist-master missing veto mention: {ban}")

    if errors:
        print("release_gate_check FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("release_gate_check OK")
    for layer, (quality, status) in sorted(as_built.items()):
        c_status, _ = checklist[layer]
        print(f"  {layer}: Q={quality} as-built={status or '?'} checklist={c_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
