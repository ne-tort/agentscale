"""Application service protocols consumed by the first web vertical slice."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .auth import WebRequestContext
from .contracts import (
    ChatRunRequest,
    ChatSidebar,
    ChatTranscript,
    ContainerStatus,
    CurrentUser,
    Project,
    RunStreamEvent,
)


class CurrentUserService(Protocol):
    async def get_current_user(self, *, context: WebRequestContext) -> CurrentUser: ...


class ProjectService(Protocol):
    async def list_projects(self, *, context: WebRequestContext, cabinet_id: str) -> tuple[Project, ...]: ...

    async def get_project(self, *, context: WebRequestContext, project_id: str) -> Project: ...


class ContainerStatusService(Protocol):
    async def get_status(self, *, context: WebRequestContext, project_id: str) -> ContainerStatus: ...


class ChatService(Protocol):
    async def get_sidebar(self, *, context: WebRequestContext, cabinet_id: str) -> ChatSidebar: ...

    async def get_transcript(
        self, *, context: WebRequestContext, project_id: str, session_id: str, after_seq: int | None = None
    ) -> ChatTranscript: ...

    async def start_run(self, *, context: WebRequestContext, request: ChatRunRequest) -> AsyncIterator[RunStreamEvent]: ...

    async def cancel_run(self, *, context: WebRequestContext, project_id: str, run_id: str) -> None: ...
