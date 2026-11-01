"""Typed integration adapter from canonical web facade to legacy services."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any

from prodavan.application.web_facade.auth import ScopeBoundary, ScopeViolation, WebRequestContext
from prodavan.application.web_facade.contracts import (
    ChatRunRequest, ChatSidebar, ChatTranscript, ContainerStatus, CurrentUser,
    Project, RunStreamEvent, SseEventEnvelope,
)

from .contracts import AdapterScope, legacy_event_to_run_event, parse_last_event_id
from .ports import LegacyServices


class WebFacadeAdapter:
    """Application-only adapter; transport and legacy routes remain untouched."""

    def __init__(self, services: LegacyServices, *, scopes: Iterable[AdapterScope] = ()) -> None:
        self._services = services
        self._scopes = frozenset(scopes)

    def _require(self, scope: AdapterScope) -> None:
        if scope not in self._scopes:
            raise ScopeViolation(f"missing web facade scope: {scope.value}")

    @staticmethod
    def _context(context: WebRequestContext) -> WebRequestContext:
        if context.user.id.strip() == "":
            raise ScopeViolation("authenticated user is required")
        return context

    async def current_user(self, *, context: WebRequestContext) -> CurrentUser:
        self._require(AdapterScope.CURRENT_USER_READ)
        return CurrentUser.model_validate(await self._services.current_user(context=self._context(context)))

    async def projects(self, *, context: WebRequestContext, cabinet_id: str) -> tuple[Project, ...]:
        self._require(AdapterScope.PROJECTS_READ)
        guarded_context = self._context(context)
        guarded_context.require_cabinet(cabinet_id)
        value = await self._services.projects(context=guarded_context, cabinet_id=cabinet_id)
        return tuple(Project.model_validate(item) for item in value)

    async def container_status(self, *, context: WebRequestContext, project_id: str) -> ContainerStatus:
        self._require(AdapterScope.CONTAINER_STATUS_READ)
        ScopeBoundary(self._context(context)).project(project_id)
        return ContainerStatus.model_validate(
            await self._services.container_status(context=context, project_id=project_id)
        )

    async def chat_sidebar(self, *, context: WebRequestContext, cabinet_id: str) -> ChatSidebar:
        self._require(AdapterScope.CHAT_SIDEBAR_READ)
        guarded_context = self._context(context)
        guarded_context.require_cabinet(cabinet_id)
        return ChatSidebar.model_validate(
            await self._services.chat_sidebar(context=guarded_context, cabinet_id=cabinet_id)
        )

    async def chat_transcript(
        self, *, context: WebRequestContext, project_id: str, session_id: str,
        after_seq: int | None = None,
    ) -> ChatTranscript:
        self._require(AdapterScope.CHAT_TRANSCRIPT_READ)
        ScopeBoundary(self._context(context)).session(project_id, session_id)
        return ChatTranscript.model_validate(
            await self._services.chat_transcript(
                context=context, project_id=project_id, session_id=session_id, after_seq=after_seq,
            )
        )

    async def run_chat(
        self, *, context: WebRequestContext, request: ChatRunRequest,
        last_event_id: str | None = None,
    ) -> AsyncIterator[SseEventEnvelope]:
        self._require(AdapterScope.CHAT_RUN)
        ScopeBoundary(self._context(context)).session(request.project_id, request.session_id)
        cursor = parse_last_event_id(last_event_id)
        async for raw in self._services.chat_run(
            context=context, request=request, after_seq=cursor.after_seq if cursor else None,
        ):
            event: RunStreamEvent
            if isinstance(raw, RunStreamEvent):
                event = raw
            else:
                event = legacy_event_to_run_event(raw, fallback_run_id=cursor.run_id)
            # A cursor is scoped to one run. Never leak a later/different run
            # into a reconnect stream merely because its sequence is larger.
            if cursor and cursor.run_id and event.run_id != cursor.run_id:
                continue
            if cursor and event.seq <= cursor.after_seq:
                continue
            yield SseEventEnvelope.from_run_event(event)
