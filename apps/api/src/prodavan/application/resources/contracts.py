"""Application contracts for unified prompts, files, and MCP resources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping


class ResourceKind(StrEnum):
    PROMPT = "prompt"
    FILE = "file"
    MCP = "mcp"


class ScopeKind(StrEnum):
    PLATFORM = "platform"
    COMPANY = "company"
    CABINET = "cabinet"
    PROJECT = "project"
    SESSION = "session"


class BindingKind(StrEnum):
    LOCAL = "local"
    GLOBAL = "global"


class VersionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class ResourceScope:
    """The owner scope of a resource, independent of its concrete payload."""

    kind: ScopeKind
    id: str


@dataclass(frozen=True, slots=True)
class ContentResource:
    """A named prompt/file/MCP resource with no mutable payload in the root."""

    id: str
    kind: ResourceKind
    slug: str
    title: str
    scope: ResourceScope
    metadata: Mapping[str, Any]
    active_version: int | None = None


@dataclass(frozen=True, slots=True)
class ResourceVersion:
    """An immutable resource payload; publishing creates a new version number."""

    id: str
    resource_id: str
    number: int
    status: VersionStatus
    payload: Mapping[str, Any]
    created_at: datetime | None = None
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceBinding:
    """A resource edge to a target scope, with explicit local/global semantics."""

    id: str
    resource_id: str
    target: ResourceScope
    binding: BindingKind
    child_may_edit: bool
    version_number: int | None = None
    project_ids: tuple[str, ...] = ()
    session_id: str | None = None

    @property
    def is_chat_scoped(self) -> bool:
        return self.session_id is not None
