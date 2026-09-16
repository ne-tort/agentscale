"""Request-scoped trace context for structured logging (audit XCUT-P2a).

The trace id is set per HTTP request by ``TraceIdMiddleware`` and made
available to structured logging via a contextvar + ``LoggerAdapter`` so log
records carry ``trace_id`` without every call site passing it explicitly.

This closes the "bound to the structured log context" claim in the
TraceIdMiddleware docstring: before, the id was only on request.state + the
response header, not in logs.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Iterator
from typing import Any

# Contextvar holds the active request trace id (or None outside a request).
_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "prodavan_trace_id", default=None
)


def current_trace_id() -> str | None:
    """Return the trace id for the current request (or None)."""
    return _trace_id.get()


def set_trace_id(trace_id: str | None) -> contextvars.Token[str | None]:
    """Set the trace id for the current async context; returns a reset token."""
    return _trace_id.set(trace_id)


def reset_trace_id(token: contextvars.Token[str | None]) -> None:
    """Reset the trace id to its previous value."""
    _trace_id.reset(token)


class TraceIdLogFilter(logging.Filter):
    """Inject ``trace_id`` into every log record emitted under this context.

    Attach to a handler (not a logger) so it applies to all child loggers.
    Records without a trace id keep going through unchanged (``extra`` is only
    added when set, so loggers that do not care about trace_id are not noisy).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = _trace_id.get()
        if trace_id:
            record.trace_id = trace_id  # type: ignore[attr-defined]
        return True


def install_trace_id_log_filter(*, target_logger: logging.Logger | None = None) -> None:
    """Install the trace-id filter on the root logger's handlers.

    Safe to call once at app bootstrap; idempotent (skips handlers that
    already carry the filter).
    """
    logger = target_logger or logging.getLogger()
    for handler in logger.handlers:
        already = any(isinstance(f, TraceIdLogFilter) for f in (handler.filters or []))
        if not already:
            handler.addFilter(TraceIdLogFilter())


def with_trace_id(trace_id: str | None) -> Iterator[None]:
    """Context manager form: set/reset the trace id for a block."""
    token = set_trace_id(trace_id)
    try:
        yield
    finally:
        reset_trace_id(token)


__all__: list[str] = [
    "TraceIdLogFilter",
    "current_trace_id",
    "install_trace_id_log_filter",
    "reset_trace_id",
    "set_trace_id",
    "with_trace_id",
]

# silence unused-import analyzers for Any
_ = Any
