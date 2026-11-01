"""Exclusive resource selection and materialization resolver zone."""

from .contracts import (
    ResourceMaterializationContext,
    ResourceMaterializationResolution,
    ResourceSelection,
    MaterializationApplyOutcome,
    ResolutionFailure,
)
from .ports import AtomicMaterializationApplyPort, ResourceMaterializationSourcePort
from .service import ResourceMaterializationResolver, apply_resolution

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
]
