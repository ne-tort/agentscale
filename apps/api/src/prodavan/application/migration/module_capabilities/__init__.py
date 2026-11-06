"""MIG-MODULE-01 application contracts.

This package intentionally exposes only ports and typed application contracts.
No legacy module-row repository, API router, runtime executor, or DSL writer is
imported here.
"""
from .contracts import *
from .dependencies import DependencyValidation, validate_module_dependencies
from .lifecycle import (
    CapabilityPublicationRepositoryPort,
    ModuleCapabilityLifecycleService,
    PublicationCommit,
    PublicationGateVerifierPort,
    PublicationOutcome,
    PublicationResult,
    TransitionCommit,
    TransitionOutcome,
    TransitionResult,
)
from .normalization import normalize_legacy_module
from .pipeline import ModuleCapabilityDraftPort, ModuleCapabilityPipeline, ModuleCapabilityPreparation

__all__ = [
    "CapabilityPublicationRepositoryPort", "DependencyValidation", "ModuleCapabilityDraftPort",
    "ModuleCapabilityLifecycleService", "ModuleCapabilityPipeline", "ModuleCapabilityPreparation",
    "PublicationCommit", "PublicationGateVerifierPort", "PublicationOutcome", "PublicationResult",
    "TransitionCommit", "TransitionOutcome", "TransitionResult", "normalize_legacy_module",
    "validate_module_dependencies",
]
