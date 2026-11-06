"""Adapter-only correlation contract for redacted MCP audit evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class McpAuditCorrelation:
    """Required MCP identifiers carried into an audit outcome."""

    mcp_trace_id: str
    span_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        for name in ("mcp_trace_id", "span_id", "correlation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")


__all__ = ["McpAuditCorrelation"]
