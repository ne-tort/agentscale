"""Fail-closed migration contracts for versioned resources."""

from .resources import (
    Binding,
    BindingState,
    CompatibilityCandidate,
    ImportOutcome,
    Resource,
    ResourceSnapshot,
    ScopeProof,
    ResourceKind,
    ResourceLifecycle,
    ResourceResolution,
    ResourceScope,
    ResourceVersion,
    SecretReference,
    ScopeKind,
    import_compatibility_candidate,
    resolve_resource,
)

__all__ = [
    "Binding", "BindingState", "CompatibilityCandidate", "ImportOutcome",
    "Resource", "ResourceSnapshot", "ScopeProof", "ResourceKind", "ResourceLifecycle", "ResourceResolution",
    "ResourceScope", "ResourceVersion", "SecretReference", "ScopeKind",
    "import_compatibility_candidate", "resolve_resource",
]
