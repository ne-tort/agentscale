"""Typed contracts and application ports for the first web vertical slice."""

from .auth import ScopeBoundary, ScopeViolation, WebRequestContext
from .contracts import (
    ChatRunRequest,
    ChatSidebar,
    ChatTranscript,
    ContainerStatus,
    CurrentUser,
    Project,
    RunStreamEvent,
    SseEventEnvelope,
)
from .ports import ChatService, ContainerStatusService, CurrentUserService, ProjectService

__all__ = [
    "ChatRunRequest",
    "ChatSidebar",
    "ChatTranscript",
    "ContainerStatus",
    "CurrentUser",
    "Project",
    "RunStreamEvent",
    "ScopeBoundary",
    "ScopeViolation",
    "SseEventEnvelope",
    "WebRequestContext",
    "ChatService",
    "ContainerStatusService",
    "CurrentUserService",
    "ProjectService",
]
