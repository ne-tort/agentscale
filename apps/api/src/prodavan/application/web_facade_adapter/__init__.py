"""Exclusive typed integration zone for the canonical web facade."""

from .contracts import (
    AdapterScope,
    LastEventIdError,
    ReplayCursor,
    legacy_event_to_run_event,
    parse_last_event_id,
)
from .context import build_stream_facade_context, resolve_web_chat_scopes
from .envelopes import (
    EncodedWireEvent,
    ParsedCursor,
    ResolvedCursor,
    RunSequenceIndex,
    StreamCursorError,
    StreamIdentityError,
    StreamPosition,
    StreamScope,
    WireEventEncoder,
    parse_transport_cursor,
    resolve_transport_cursor,
)
from .framing import (
    CursorLedger,
    StreamStats,
    serialize_comment,
    serialize_frame,
    stream_frames,
    terminal_error_frame,
)
from .service import WebFacadeAdapter
from .sessions import ChatSessionResolver, ResolvedChatSession, SqlChatSessionResolver
from .streams import (
    AdapterChatStreamFacade,
    ChatStreamCommand,
    UnavailableChatStreamFacade,
    UnavailableRunIndexProvider,
    WebChatStreamFacade,
    WebStreamRuntime,
    require_stream_scope,
)

__all__ = [
    "AdapterChatStreamFacade",
    "AdapterScope",
    "ChatSessionResolver",
    "ChatStreamCommand",
    "CursorLedger",
    "EncodedWireEvent",
    "LastEventIdError",
    "ParsedCursor",
    "ReplayCursor",
    "ResolvedChatSession",
    "ResolvedCursor",
    "RunSequenceIndex",
    "SqlChatSessionResolver",
    "StreamCursorError",
    "StreamIdentityError",
    "StreamPosition",
    "StreamScope",
    "StreamStats",
    "UnavailableChatStreamFacade",
    "UnavailableRunIndexProvider",
    "WebChatStreamFacade",
    "WebFacadeAdapter",
    "WebStreamRuntime",
    "build_stream_facade_context",
    "legacy_event_to_run_event",
    "parse_last_event_id",
    "parse_transport_cursor",
    "require_stream_scope",
    "resolve_transport_cursor",
    "resolve_web_chat_scopes",
    "serialize_comment",
    "serialize_frame",
    "stream_frames",
    "terminal_error_frame",
]
