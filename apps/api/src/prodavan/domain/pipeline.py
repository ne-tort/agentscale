"""Spec run pipeline phases and guards (INV-SKP-002, INV-SKP-004)."""

from __future__ import annotations

PHASES = (
    "ingest",
    "classify",
    "search",
    "rank",
    "variants",
    "review",
    "final",
)

ARTIFACT_FOR_PHASE: dict[str, str] = {
    "classify": "rows.json",
    "search": "lineitems.json",
    "rank": "offers.json",
    "variants": "selection.json",
    "review": "commerce.sqlite",
}

ALLOWED_FORWARD: dict[str, str] = {
    "ingest": "classify",
    "classify": "search",
    "search": "rank",
    "rank": "variants",
    "variants": "review",
    "review": "final",
}

RESEARCH_FROM = "review"
RESEARCH_TO = "search"


def next_phase(current: str) -> str | None:
    return ALLOWED_FORWARD.get(current)


def artifact_required_for(target_phase: str) -> str | None:
    return ARTIFACT_FOR_PHASE.get(target_phase)


def can_advance(*, current: str, target: str, from_review_research: bool = False) -> bool:
    if from_review_research and current == RESEARCH_FROM and target == RESEARCH_TO:
        return True
    return ALLOWED_FORWARD.get(current) == target


def can_finalize(current: str) -> bool:
    return current == "review"
