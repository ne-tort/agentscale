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
_CHECKLIST_ROW_RE = re.compile(
    r"\|\s*\[(L\d{2})\][^|]*\|\s*(\w+)\s*\|\s*(\d+)\s*\|",
    re.MULTILINE,
)


def _as_built_qualities() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(AS_BUILT.glob("L*.md")):
        m = _LAYER_RE.match(path.stem)
        if not m:
            continue
        layer = f"L{m.group(1)}"
        text = path.read_text(encoding="utf-8", errors="replace")
        qm = _QUALITY_RE.search(text)
        if qm:
            out[layer] = int(qm.group(1))
    return out


def _checklist_rows() -> dict[str, tuple[str, int]]:
    text = CHECKLIST.read_text(encoding="utf-8", errors="replace")
    out: dict[str, tuple[str, int]] = {}
    for m in _CHECKLIST_ROW_RE.finditer(text):
        out[m.group(1)] = (m.group(2), int(m.group(3)))
    return out


def main() -> int:
    errors: list[str] = []
    as_built = _as_built_qualities()
    checklist = _checklist_rows()

    if not as_built:
        errors.append("no as-built Quality scores found")
    if not checklist:
        errors.append("no checklist-master layer rows found")

    for layer, quality in sorted(as_built.items()):
        if layer not in checklist:
            errors.append(f"{layer}: in as-built (Q={quality}) but missing from checklist-master")
            continue
        status, cq = checklist[layer]
        if cq != quality:
            errors.append(
                f"{layer}: checklist Quality={cq} drifts from as-built Quality={quality} (status={status})"
            )

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
    for layer, quality in sorted(as_built.items()):
        status, _ = checklist[layer]
        print(f"  {layer}: Q={quality} status={status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
