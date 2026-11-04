"""Wire DTOs for the first web vertical slice.

These models are deliberately independent from SQLAlchemy rows and existing route
payloads. They are the stable boundary consumed by the new web application.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, use_enum_values=True)


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"


class ContainerLifecycle(StrEnum):
    UNKNOWN = "unknown"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class ChatSessionStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ChatBlockKind(StrEnum):
    USER = "user"
    ASSISTANT_MARKDOWN = "assistant_markdown"
    TOOL = "tool"
    THINKING = "thinking"
    PLAN = "plan"
    SUBAGENT = "subagent"
    USAGE = "usage"


class RunEventType(StrEnum):
    STARTED = "run_started"
    TEXT_DELTA = "text_delta"
    BLOCK = "block"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPLETED = "run_completed"
    FAILED = "run_failed"
    CANCELLED = "run_cancelled"
    HEARTBEAT = "heartbeat"


def event_type_name(value: RunEventType | str) -> str:
    """Wire name of a run event type, accepting both shapes this type takes.

    ``ApiModel`` sets ``use_enum_values=True``, so a *validated*
    ``RunStreamEvent.type`` is already the plain string (``"run_started"``) and
    reading ``.value`` off it raises ``AttributeError``. A model built without
    validation (``model_construct``) or a caller passing the enum member still
    hands over a ``RunEventType``. Both are legitimate, so every reader goes
    through this one function instead of assuming either shape — the mistake
    that made the very first canonical SSE frame blow up.

    ``use_enum_values`` itself stays: it defines the outward JSON contract.
    Anything that is neither an enum member nor a non-empty string is refused
    with ``ValueError`` so the transport can map it to a stable problem code.
    """
    name = getattr(value, "value", value)
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"run event type must be an enum member or a name, got {type(value).__name__}")
    return name


class CurrentUser(ApiModel):
    id: str
    email: str | None = None
    display_name: str | None = None
    company_id: str | None = None
    roles: tuple[str, ...] = ()
    is_platform_admin: bool = False


class Project(ApiModel):
    id: str
    cabinet_id: str
    name: str
    status: ProjectStatus
    visibility: Literal["cabinet", "restricted"] = "cabinet"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ContainerStatus(ApiModel):
    project_id: str
    lifecycle: ContainerLifecycle
    desired: Literal["running", "stopped"]
    observed: str | None = None
    ready: bool = False
    endpoint: str | None = None
    last_error: str | None = None
    updated_at: datetime | None = None


class ChatSidebarItem(ApiModel):
    session_id: str
    project_id: str
    project_name: str
    title: str | None = None
    pinned: bool = False
    has_draft: bool = False
    has_messages: bool = False
    last_message_at: datetime | None = None
    status: ChatSessionStatus


class ChatSidebarProject(ApiModel):
    project: Project
    chats: tuple[ChatSidebarItem, ...] = ()
    new_chat_enabled: bool = False


class ChatSidebar(ApiModel):
    cabinet_id: str
    selected_project_id: str | None = None
    selected_session_id: str | None = None
    projects: tuple[ChatSidebarProject, ...] = ()


class ChatBlock(ApiModel):
    seq: int
    kind: ChatBlockKind
    content: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatTranscript(ApiModel):
    project_id: str
    session_id: str
    status: ChatSessionStatus
    blocks: tuple[ChatBlock, ...] = ()
    turn_in_progress: bool = False
    oldest_seq: int | None = None
    has_more: bool = False


class ChatRunRequest(ApiModel):
    project_id: str
    session_id: str
    prompt: str = Field(min_length=1, max_length=500_000)
    model_id: str | None = None


class RunStreamEvent(ApiModel):
    type: RunEventType
    run_id: str
    seq: int
    text: str | None = None
    block: ChatBlock | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class SseEventEnvelope(ApiModel):
    """Transport envelope; ``data`` is JSON-safe and event names are stable."""

    id: str
    event: str
    data: RunStreamEvent
    retry_ms: int | None = Field(default=None, ge=0)

    @classmethod
    def from_run_event(cls, event: RunStreamEvent) -> "SseEventEnvelope":
        return cls(id=f"{event.run_id}:{event.seq}", event=event_type_name(event.type), data=event)
