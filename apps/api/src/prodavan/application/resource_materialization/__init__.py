"""Exclusive resource selection and materialization resolver zone.

The apply seam (MIG-RES-APPLYPORT-01) is exported lazily: importing
``apply_adapter`` pulls in SQLAlchemy models and the resource API contracts, while
this package is itself imported by the resource adapter factory. Module-level
``__getattr__`` keeps the ordinary resolver imports light and free of import cycles,
and still gives callers a first-class name::

    from prodavan.application.resource_materialization import (
        build_resource_materialization_apply_port,
    )
"""

from __future__ import annotations

from typing import Any

from .contracts import (
    ResourceMaterializationContext,
    ResourceMaterializationResolution,
    ResourceSelection,
    MaterializationApplyOutcome,
    ResolutionFailure,
)
from .ports import AtomicMaterializationApplyPort, ResourceMaterializationSourcePort
from .service import ResourceMaterializationResolver, apply_resolution

_LAZY_EXPORTS = frozenset(
    {
        "EVIDENCE_AUTHORITY_BLOCKERS",
        "ApprovalEvidence",
        "OperationGrounding",
        "ResourceApplyEvidence",
        "ResourceApplyEvidenceGate",
        "ResourceApplyEvidencePolicy",
        "ResourceApplyEvidenceSource",
        "ResourceMaterializationApplyPort",
        "SqlAlchemyResourceApplyEvidenceReader",
        "build_resource_materialization_apply_port",
    }
)


def __getattr__(name: str) -> Any:
    """Resolve the apply-adapter surface on first use (PEP 562)."""
    if name in _LAZY_EXPORTS:
        from . import apply_adapter

        return getattr(apply_adapter, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__))


__all__ = [
    "AtomicMaterializationApplyPort",
    "MaterializationApplyOutcome",
    "ResourceMaterializationContext",
    "ResourceMaterializationResolution",
    "ResourceMaterializationResolver",
    "ResourceMaterializationSourcePort",
    "apply_resolution",
    "ResourceSelection",
    "ResolutionFailure",
    "EVIDENCE_AUTHORITY_BLOCKERS",
    "ApprovalEvidence",
    "OperationGrounding",
    "ResourceApplyEvidence",
    "ResourceApplyEvidenceGate",
    "ResourceApplyEvidencePolicy",
    "ResourceApplyEvidenceSource",
    "ResourceMaterializationApplyPort",
    "SqlAlchemyResourceApplyEvidenceReader",
    "build_resource_materialization_apply_port",
]
