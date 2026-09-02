"""Agent runtime domain types (L08)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class AgentEventType(StrEnum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_APPROVAL_REQUEST = "tool_approval_request"
    USAGE = "usage"
    ERROR = "error"
    DONE = "done"


# Frozen adapter stream types (canon adapter-port). Platform may also persist
# USER_MESSAGE for chat transcript — not emitted by AgentProviderPort adapters.
FROZEN_EVENT_TYPES = frozenset(AgentEventType)
PLATFORM_EVENT_USER_MESSAGE = "user_message"
PLATFORM_EVENT_TOOL_APPROVAL_DECISION = "tool_approval_decision"


class AgentSessionStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"
    CANCELLED = "cancelled"


RESUMABLE_SESSION_STATUSES = frozenset(
    {
        AgentSessionStatus.ACTIVE,
        AgentSessionStatus.SUSPENDED,
    }
)


@dataclass(frozen=True, slots=True)
class AgentEvent:
    type: str
    data: dict[str, Any]
    at: str | None = None

    def __post_init__(self) -> None:
        if self.type not in FROZEN_EVENT_TYPES:
            raise ValueError(f"unknown AgentEvent type: {self.type}")

    @staticmethod
    def now(type: str, data: dict[str, Any]) -> AgentEvent:
        return AgentEvent(type=type, data=data, at=datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "data": self.data}
        if self.at:
            out["at"] = self.at
        return out


@dataclass(frozen=True, slots=True)
class AgentHandle:
    id: str
    provider: str
    cwd: str
    model: str | None = None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    text: str
    attachment_refs: tuple[str, ...] = ()
    images: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class AgentToolPolicy:
    fs_read: bool = True
    fs_write: bool = True
    fs_delete: bool = False
    shell_exec: bool = True
    network: str = "allowlist"
    network_allowlist: tuple[str, ...] = ()
    mcp: str = "manifest_only"
    mcp_allowlist: tuple[str, ...] = ()
    sandbox: str = "workspace"
    approval: str = "dangerous_only"
    extra_deny_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CreateOpts:
    cwd: str
    model: str | None
    mcp_servers: dict[str, Any]
    api_key: str
    api_kind: str
    provider: str
    tool_policy: AgentToolPolicy
    budget: dict[str, Any] | None = None
    setting_sources: tuple[str, ...] = ("project",)


TOOL_PRESET_POLICIES: dict[str, AgentToolPolicy] = {
    "chat_readonly": AgentToolPolicy(
        fs_read=True,
        fs_write=False,
        fs_delete=False,
        shell_exec=False,
        network="deny",
        mcp="manifest_only",
        sandbox="strict",
        approval="none",
    ),
    "workspace_dev": AgentToolPolicy(
        fs_read=True,
        fs_write=True,
        shell_exec=True,
        network="allowlist",
        mcp="manifest_only",
        sandbox="workspace",
        approval="dangerous_only",
    ),
    "workspace_full": AgentToolPolicy(
        fs_read=True,
        fs_write=True,
        fs_delete=True,
        shell_exec=True,
        network="allow",
        mcp="allowlist",
        sandbox="workspace",
        approval="none",
    ),
}


def default_tool_policy(preset: str = "workspace_dev") -> AgentToolPolicy:
    return TOOL_PRESET_POLICIES.get(preset, TOOL_PRESET_POLICIES["workspace_dev"])
