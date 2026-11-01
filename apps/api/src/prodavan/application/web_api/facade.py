"""Protocol for the first web vertical-slice facade.

Implementations may delegate to the existing application services. The
protocol is intentionally independent from FastAPI and database sessions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from prodavan.application.web_api.contracts import (
    ChatRunRequest,
    ChatSidebarResponse,
    ChatTranscriptResponse,
    ContainerStatusResponse,
    CurrentUserResponse,
    ProjectsResponse,
    SseEventEnvelope,
)
from prodavan.application.web_api.dependencies import WebApiAuthContext


class WebApiFacade(Protocol):
    async def current_user(self, auth: WebApiAuthContext) -> CurrentUserResponse:
        """Return the authenticated employee projection."""

    async def projects(self, auth: WebApiAuthContext, *, cabinet_id: str) -> ProjectsResponse:
        """List projects visible to the active employee in a cabinet."""

    async def container_status(
        self, auth: WebApiAuthContext, *, project_id: str
    ) -> ContainerStatusResponse:
        """Return desired and observed runtime state for one project."""

    async def chat_sidebar(
        self, auth: WebApiAuthContext, *, cabinet_id: str
    ) -> ChatSidebarResponse:
        """Return project-grouped chats and persistent selections."""

    async def chat_transcript(
        self,
        auth: WebApiAuthContext,
        *,
        project_id: str,
        session_id: str,
        limit: int = 500,
        before_seq: int | None = None,
        after_seq: int | None = None,
    ) -> ChatTranscriptResponse:
        """Return block transcript with pagination and in-progress state."""

    async def run_chat(
        self, auth: WebApiAuthContext, request: ChatRunRequest
    ) -> AsyncIterator[SseEventEnvelope]:
        """Start one turn and yield reconnectable SSE envelopes."""
