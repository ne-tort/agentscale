"""Typed contracts for attaching MCP capabilities to project runtimes.

The module is deliberately transport- and persistence-neutral.  It captures the
identity that must be locked before a runtime starts, while policy evaluation is
kept in :mod:`policy`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Mapping


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _required(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value.strip()


class McpDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class McpPolicyCode(StrEnum):
    ALLOWED = "allowed"
    SERVER_NOT_REGISTERED = "server_not_registered"
    ATTACHMENT_NOT_FOUND = "attachment_not_found"
    PACKAGE_IDENTITY_MISMATCH = "package_identity_mismatch"
    TOOL_NOT_ALLOWLISTED = "tool_not_allowlisted"
    PROFILE_NOT_ALLOWED = "profile_not_allowed"
    PROJECT_NOT_ALLOWED = "project_not_allowed"


@dataclass(frozen=True, slots=True)
class McpPackage:
    """Immutable package identity used for reproducible runtime locking."""

    name: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        _required(self.name, "package name")
        _required(self.version, "package version")
        digest = self.digest.removeprefix("sha256:").lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("package digest must be a 64-character sha256 hex digest")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "version", self.version.strip())
        object.__setattr__(self, "digest", digest)

    @property
    def qualified_digest(self) -> str:
        return f"sha256:{self.digest}"


@dataclass(frozen=True, slots=True)
class McpServerRegistration:
    """Registered MCP server and the tools it declares."""

    server_id: str
    package: McpPackage
    declared_tools: tuple[str, ...]
    display_name: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required(self.server_id, "server_id")
        normalized_tools = tuple(tool.strip() for tool in self.declared_tools)
        if not normalized_tools or any(not tool for tool in normalized_tools):
            raise ValueError("declared_tools must contain at least one non-empty tool")
        if len(set(normalized_tools)) != len(normalized_tools):
            raise ValueError("declared_tools must be unique")
        object.__setattr__(self, "server_id", self.server_id.strip())
        object.__setattr__(self, "declared_tools", normalized_tools)


@dataclass(frozen=True, slots=True)
class McpToolAllowlist:
    """Tools enabled for one server attachment."""

    server_id: str
    tools: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(self.server_id, "server_id")
        normalized_tools = tuple(tool.strip() for tool in self.tools)
        if any(not tool for tool in normalized_tools):
            raise ValueError("allowlisted tools must not be empty")
        if len(set(normalized_tools)) != len(normalized_tools):
            raise ValueError("allowlisted tools must be unique")
        object.__setattr__(self, "server_id", self.server_id.strip())
        object.__setattr__(self, "tools", normalized_tools)

    def allows(self, tool: str) -> bool:
        return tool in self.tools


@dataclass(frozen=True, slots=True)
class McpAttachment:
    """Project/profile binding for a registered MCP server."""

    attachment_id: str
    project_id: str
    profile_id: str
    server_id: str
    package: McpPackage
    allowlist: McpToolAllowlist
    enabled: bool = True

    def __post_init__(self) -> None:
        for name, value in (("attachment_id", self.attachment_id), ("project_id", self.project_id), ("profile_id", self.profile_id), ("server_id", self.server_id)):
            _required(value, name)
        if self.allowlist.server_id != self.server_id:
            raise ValueError("allowlist server_id must match attachment server_id")


@dataclass(frozen=True, slots=True)
class McpPolicyRequest:
    project_id: str
    profile_id: str
    server_id: str
    package: McpPackage
    tool: str

    def __post_init__(self) -> None:
        for name, value in (("project_id", self.project_id), ("profile_id", self.profile_id), ("server_id", self.server_id), ("tool", self.tool)):
            _required(value, name)


@dataclass(frozen=True, slots=True)
class McpPolicyDecision:
    decision: McpDecision
    code: McpPolicyCode
    reason: str | None = None
    attachment_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is McpDecision.ALLOW


@dataclass(frozen=True, slots=True)
class McpRuntimeLockEntry:
    """The complete MCP identity and tool set passed to a runtime."""

    lock_id: str
    attachment_id: str
    project_id: str
    profile_id: str
    server_id: str
    package: McpPackage
    tools: tuple[str, ...]
    policy_code: McpPolicyCode = McpPolicyCode.ALLOWED

    def __post_init__(self) -> None:
        for name, value in (("lock_id", self.lock_id), ("attachment_id", self.attachment_id), ("project_id", self.project_id), ("profile_id", self.profile_id), ("server_id", self.server_id)):
            _required(value, name)
        if not self.tools or any(not tool.strip() for tool in self.tools):
            raise ValueError("runtime lock must contain at least one tool")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("runtime lock tools must be unique")
        object.__setattr__(self, "tools", tuple(self.tools))
        if self.policy_code is not McpPolicyCode.ALLOWED:
            raise ValueError("runtime lock can only contain an allowed policy decision")
