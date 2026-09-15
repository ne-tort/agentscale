"""Cross-module prompt fragment stitch by workspace path + priority."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptContribution:
    workspace_path: str
    priority: int
    body: str
    module_id: str = ""
    fragment_id: str = ""


def stitch_prompt_contributions(items: list[PromptContribution]) -> dict[str, str]:
    """Group by workspace_path, sort, join bodies with a blank line.

    Order within a file: ascending ``priority``, then shorter ``body``, then
    stable ``fragment_id`` / ``module_id``.
    """
    grouped: dict[str, list[PromptContribution]] = defaultdict(list)
    for item in items:
        path = (item.workspace_path or "").strip()
        if not path:
            continue
        grouped[path].append(item)

    out: dict[str, str] = {}
    for path, contribs in grouped.items():
        ordered = sorted(
            contribs,
            key=lambda c: (
                c.priority,
                len(c.body),
                c.fragment_id or "",
                c.module_id or "",
            ),
        )
        out[path] = "\n\n".join(c.body for c in ordered)
    return out
