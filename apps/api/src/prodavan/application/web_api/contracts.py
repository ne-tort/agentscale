"""Typed API facade contracts for the first web vertical slice.

These models describe the wire contract only. Existing v1 routes remain the
source of behavior until a facade adapter is introduced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from prodavan.application.web_facade.contracts import (
    ChatRunRequest as FacadeChatRunRequest,
    CurrentUser as FacadeCurrentUser,
    RunStreamEvent as FacadeRunStreamEvent,
)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CurrentUserResponse(_Contract):
    """Transport adapter for the canonical ``web_facade.CurrentUser``."""

    id: str = Field(min_length=1, max_length=64)
    subject: str = Field(min_length=1, max_length=255)
    email: str | None = None
    display_name: str | None = None
    roles: tuple[str, ...] = ()
    company_id: str | None = None
    employee_id: str | None = None
    active: bool = True

    def to_facade(self) -> FacadeCurrentUser:
        return FacadeCurrentUser(
            id=self.id,
            email=self.email,
            display_name=self.display_name,
            company_id=self.company_id,
            roles=self.roles,
            is_platform_admin="platform.admin" in self.roles,
        )


class ProjectLifecycle(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"


class ProjectSummary(_Contract):
    id: str = Field(min_length=1, max_length=64)
    cabinet_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    status: ProjectLifecycle
    about: str | None = None
    selected: bool = False
    new_chat_enabled: bool = False
    observed_state: str | None = None
    updated_at: datetime | None = None


class ProjectsResponse(_Contract):
    items: tuple[ProjectSummary, ...] = ()
    selected_project_id: str | None = None


class ContainerStatus(StrEnum):
    UNKNOWN = "unknown"
    PREPARING = "preparing"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class ContainerStatusResponse(_Contract):
    project_id: str = Field(min_length=1, max_length=64)
    container_id: str | None = None
    status: ContainerStatus
    desired_state: str | None = None
    observed_state: str | None = None
    runtime_ref: str | None = None
    last_error: str | None = None
    updated_at: datetime | None = None


class ChatSidebarItem(_Contract):
    session_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    project_name: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    pinned: bool = False
    has_draft: bool = False
    has_messages: bool = False
    last_message_at: datetime | None = None
    status: str | None = None


class ChatSidebarProject(_Contract):
    project_id: str = Field(min_length=1, max_length=64)
    project_name: str = Field(min_length=1, max_length=200)
    active: bool = True
    new_chat_enabled: bool = False
    chats: tuple[ChatSidebarItem, ...] = ()


class ChatSidebarResponse(_Contract):
    cabinet_id: str = Field(min_length=1, max_length=64)
    selected_project_id: str | None = None
    selected_session_id: str | None = None
    projects: tuple[ChatSidebarProject, ...] = ()
    # Kept for clients that still consume the pre-projects rail response.
    project_chats: tuple[ChatSidebarItem, ...] = ()


class TranscriptBlock(_Contract):
    kind: str = Field(min_length=1, max_length=64)
    text: str | None = None
    seq: int | None = Field(default=None, ge=0)
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatTranscriptResponse(_Contract):
    session_id: str = Field(min_length=1, max_length=64)
    blocks: tuple[TranscriptBlock, ...] = ()
    oldest_seq: int | None = Field(default=None, ge=0)
    newest_seq: int | None = Field(default=None, ge=0)
    has_more: bool = False
    turn_in_progress: bool = False


class ChatRunRequest(_Contract):
    """Compatibility wire request; the application port uses ``prompt``."""

    project_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=500_000)
    turn_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    attachment_refs: tuple[str, ...] = ()

    def to_facade(self) -> FacadeChatRunRequest:
        return FacadeChatRunRequest(
            project_id=self.project_id,
            session_id=self.session_id,
            prompt=self.text,
            model_id=self.model,
        )


class SseEventType(StrEnum):
    RUN_STARTED = "run_started"
    TEXT_DELTA = "text_delta"
    TRANSCRIPT_BLOCK = "transcript_block"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATUS = "status"
    ERROR = "error"
    DONE = "done"
    KEEPALIVE = "keepalive"


class SseEventEnvelope(_Contract):
    """Compatibility SSE envelope; application events are ``RunStreamEvent``."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: SseEventType
    schema_version: int = Field(default=1, ge=1)
    occurred_at: datetime
    project_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    run_id: str | None = Field(default=None, max_length=64)
    sequence: int | None = Field(default=None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    # Sequence cursor for replay; transport Last-Event-ID carries event_id.
    resume_after: int | None = Field(default=None, ge=0)

    @property
    def sse_event_name(self) -> str:
        return self.event_type.value

    @classmethod
    def from_facade_event(
        cls,
        event: FacadeRunStreamEvent,
        *,
        project_id: str,
        session_id: str,
    ) -> "SseEventEnvelope":
        event_map = {
            "run_started": SseEventType.RUN_STARTED,
            "text_delta": SseEventType.TEXT_DELTA,
            "block": SseEventType.TRANSCRIPT_BLOCK,
            "tool_call": SseEventType.TOOL_CALL,
            "tool_result": SseEventType.TOOL_RESULT,
            "run_completed": SseEventType.DONE,
            "run_failed": SseEventType.ERROR,
            "run_cancelled": SseEventType.STATUS,
            "heartbeat": SseEventType.KEEPALIVE,
        }
        payload = dict(event.data)
        if event.text is not None:
            payload["text"] = event.text
        if event.block is not None:
            payload["block"] = event.block.model_dump(mode="json")
        return cls(
            event_id=uuid5_from_run(event.run_id, event.seq),
            event_type=event_map[event.type.value],
            occurred_at=datetime.now(timezone.utc),
            project_id=project_id,
            session_id=session_id,
            run_id=event.run_id,
            sequence=event.seq,
            payload=payload,
            resume_after=event.seq,
        )


def uuid5_from_run(run_id: str, sequence: int) -> UUID:
    """Stable transport id for reconnects without changing the app event model."""
    from uuid import NAMESPACE_URL, uuid5

    return uuid5(NAMESPACE_URL, f"prodavan:web-run:{run_id}:{sequence}")
