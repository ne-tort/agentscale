"""Normalize agent stream events at platform ingress (cumulative SDK → incremental wire)."""

from __future__ import annotations

from prodavan.application.agent.text_delta import normalize_text_delta
from prodavan.domain.agent import AgentEvent, AgentEventType


class TurnStreamNormalizer:
    """Stateful per-turn normalizer for text/thinking deltas."""

    def __init__(self) -> None:
        self._text_cumulative = ""
        self._thinking_cumulative = ""

    def reset(self) -> None:
        self._text_cumulative = ""
        self._thinking_cumulative = ""

    def normalize_event(self, event: AgentEvent) -> AgentEvent | None:
        """Return incremental event, or None when delta adds no new text."""
        if event.type == AgentEventType.TEXT_DELTA:
            chunk = str(event.data.get("text") or "")
            incremental, self._text_cumulative = normalize_text_delta(self._text_cumulative, chunk)
            if not incremental:
                return None
            return AgentEvent(
                type=event.type,
                data={**event.data, "text": incremental},
                at=event.at,
            )
        if event.type == AgentEventType.THINKING_DELTA:
            self._text_cumulative = ""
            chunk = str(event.data.get("text") or "")
            incremental, self._thinking_cumulative = normalize_text_delta(
                self._thinking_cumulative, chunk
            )
            if not incremental:
                return None
            return AgentEvent(
                type=event.type,
                data={**event.data, "text": incremental},
                at=event.at,
            )
        if event.type in (
            AgentEventType.TOOL_CALL,
            AgentEventType.DONE,
        ):
            self._text_cumulative = ""
        if event.type == AgentEventType.THINKING_COMPLETE:
            self._thinking_cumulative = ""
        return event
