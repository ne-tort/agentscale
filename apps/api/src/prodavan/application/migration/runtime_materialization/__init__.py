"""Fail-closed runtime materialization contracts for MIG-RES-02.

No production adapter is provided here. Callers must supply atomic stores and
all dependency evidence; the default planner/apply path denies promotion.
"""

from .materialization import (
    ApplyOutcome,
    ApplyResult,
    AppliedRevision,
    DesiredRevision,
    DependencyEvidence,
    MaterializationPlan,
    PlanAction,
    RollbackCommand,
    plan_materialization,
    apply_materialization,
)

__all__ = [
    "ApplyOutcome", "ApplyResult", "AppliedRevision", "DesiredRevision",
    "DependencyEvidence", "MaterializationPlan", "PlanAction", "RollbackCommand",
    "plan_materialization", "apply_materialization",
]
