"""Typed resource/content domain contracts."""

from prodavan.application.resources.contracts import (
    BindingKind,
    ContentResource,
    ResourceBinding,
    ResourceKind,
    ResourceScope,
    ResourceVersion,
    ScopeKind,
    VersionStatus,
)
from prodavan.application.resources.ports import ResourceRepository

__all__ = [
    "BindingKind",
    "ContentResource",
    "ResourceBinding",
    "ResourceKind",
    "ResourceRepository",
    "ResourceScope",
    "ResourceVersion",
    "ScopeKind",
    "VersionStatus",
]
