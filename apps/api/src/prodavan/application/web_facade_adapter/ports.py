"""Legacy service protocols used by the typed web facade adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from prodavan.application.web_facade.auth import WebRequestContext
from prodavan.application.web_facade.contracts import ChatRunRequest


class LegacyServices(Protocol):
    async def current_user(self, *, context: WebRequestContext) -> Any: ...
    async def projects(self, *, context: WebRequestContext, cabinet_id: str) -> Any: ...
    async def container_status(self, *, context: WebRequestContext, project_id: str) -> Any: ...
    async def chat_sidebar(self, *, context: WebRequestContext, cabinet_id: str) -> Any: ...
    async def chat_transcript(
        self, *, context: WebRequestContext, project_id: str, session_id: str,
        after_seq: int | None = None,
    ) -> Any: ...
    async def chat_run(
        self, *, context: WebRequestContext, request: ChatRunRequest,
        after_seq: int | None = None,
    ) -> AsyncIterator[object]: ...
