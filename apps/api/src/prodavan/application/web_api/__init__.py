"""Transport compatibility adapter for the canonical ``web_facade`` boundary.

The package has no route registration and does not change established v1 handlers.
Its DTOs translate wire payloads to/from ``application.web_facade`` contracts.
"""

from prodavan.application.web_api.contracts import (
    ChatRunRequest,
    ChatSidebarResponse,
    ChatTranscriptResponse,
    ContainerStatus,
    ContainerStatusResponse,
    CurrentUserResponse,
    ProjectSummary,
    ProjectsResponse,
    SseEventEnvelope,
    SseEventType,
)
from prodavan.application.web_api.dependencies import (
    WEB_API_SCOPES,
    WebApiAuthContext,
    WebApiScope,
)
from prodavan.application.web_api.facade import WebApiFacade

__all__ = [
    "ChatRunRequest",
    "ChatSidebarResponse",
    "ChatTranscriptResponse",
    "ContainerStatus",
    "ContainerStatusResponse",
    "CurrentUserResponse",
    "ProjectSummary",
    "ProjectsResponse",
    "SseEventEnvelope",
    "SseEventType",
    "WEB_API_SCOPES",
    "WebApiAuthContext",
    "WebApiScope",
    "WebApiFacade",
]
