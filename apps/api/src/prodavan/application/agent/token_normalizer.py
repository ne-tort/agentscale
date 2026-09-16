"""Normalize agent usage events before persistence (L03, audit CLAW-P0b).

Vendor adapters report token usage with different semantics:

* Claude emits per-step ``output_tokens`` (cumulative placeholder in some
  SDKs) and exposes ``cache_creation_input_tokens`` /
  ``cache_read_input_tokens`` plus a ``message_id`` for idempotency.
* OpenAI passthrough reports final totals once.
* Cursor SDK may report cumulative totals that, if summed naively,
  double-count.

Problems this normalizer fixes (CLAW-P0b):

1. **No cache tokens** — ``cache_creation`` / ``cache_read`` were dropped,
   so prompt-cache savings were invisible to budget/metrics.
2. **No dedupe by ``message_id``** — a retried/re-emitted usage event could
   be persisted twice, inflating the month budget.
3. **Zeroed usage wipes last non-zero** — some SDKs emit a trailing USAGE
   with ``0`` tokens; naively persisting it overwrote the real row. We skip
   fully-zero usage (no input/output/cache + no cost) unless it carries a
   new ``message_id`` we have not seen.

The normalizer is stateful per turn (like ``TurnStreamNormalizer``) and is
applied by the single send-path helper in ``session_service``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prodavan.domain.agent import AgentEvent, AgentEventType


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return None
    return ivalue if ivalue >= 0 else None


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    """Normalized USAGE payload ready for ``AgentUsageRow`` + metrics.

    ``to_payload`` projects the normalized fields (plus any extra passthrough
    keys the bridge sent) back into a dict for ``AgentEventRow.payload`` and
    SSE, so the persisted transcript no longer carries vendor aliases like
    ``cache_creation_input_tokens`` / ``estimated_cost_usd``.
    """

    input_tokens: int | None
    output_tokens: int | None
    cache_creation_tokens: int | None
    cache_read_tokens: int | None
    cost_usd: float | None
    provider: str | None
    model: str | None
    message_id: str | None
    token_source: str | None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
        }
        if self.cost_usd is not None:
            payload["cost_usd"] = self.cost_usd
        if self.provider:
            payload["provider"] = self.provider
        if self.model:
            payload["model"] = self.model
        if self.message_id:
            payload["message_id"] = self.message_id
        if self.token_source:
            payload["token_source"] = self.token_source
        return payload

    def is_fully_zero(self) -> bool:
        numeric = (
            self.input_tokens,
            self.output_tokens,
            self.cache_creation_tokens,
            self.cache_read_tokens,
            self.cost_usd,
        )
        return all(value in (None, 0) for value in numeric)


# Vendor aliases that ``extract`` collapses into canonical fields. Kept as
# a module constant so the normalization contract is in one place.
_USAGE_FIELD_ALIASES = {
    "cache_creation_tokens": "cache_creation_input_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
    "cost_usd": "estimated_cost_usd",
    "message_id": "request_id",
    "token_source": "usage_source",
}


def _extract_message_id(data: dict[str, Any]) -> str | None:
    raw = data.get("message_id") or data.get("request_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _extract_cost(data: dict[str, Any]) -> float | None:
    raw = data.get("cost_usd")
    if raw is None:
        raw = data.get("estimated_cost_usd")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _extract_token_source(data: dict[str, Any]) -> str | None:
    raw = data.get("token_source") or data.get("usage_source")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def extract_usage(data: dict[str, Any]) -> NormalizedUsage:
    """Project a raw vendor USAGE payload into canonical fields (CLAW-P0b).

    Single source of truth for field extraction + alias collapsing. Both
    ``TokenNormalizer.normalize_usage`` (dedupe + zero-skip) and callers
    that only need field projection go through here, so the canonical
    schema cannot drift between the two paths.
    """
    return NormalizedUsage(
        input_tokens=_as_int(data.get("input_tokens")),
        output_tokens=_as_int(data.get("output_tokens")),
        cache_creation_tokens=_as_int(
            data.get("cache_creation_tokens") or data.get("cache_creation_input_tokens")
        ),
        cache_read_tokens=_as_int(
            data.get("cache_read_tokens") or data.get("cache_read_input_tokens")
        ),
        cost_usd=_extract_cost(data),
        provider=str(data["provider"]) if data.get("provider") else None,
        model=str(data["model"]) if data.get("model") else None,
        message_id=_extract_message_id(data),
        token_source=_extract_token_source(data),
    )


class TokenNormalizer:
    """Stateful per-turn usage normalizer (CLAW-P0b)."""

    def __init__(self) -> None:
        self._seen_message_ids: set[str] = set()
        self._last_nonzero: NormalizedUsage | None = None

    def reset(self) -> None:
        self._seen_message_ids.clear()
        self._last_nonzero = None

    def normalize_usage(self, data: dict[str, Any]) -> NormalizedUsage | None:
        """Return normalized usage, or ``None`` when the event should be skipped.

        Skip rules (fail-safe: never inflate the budget):

        * duplicate ``message_id`` already seen this turn → ``None``;
        * fully-zero usage (no input/output/cache, no cost) → ``None`` so it
          cannot overwrite the last real row via a subsequent persist path.
        """
        message_id = _extract_message_id(data)
        if message_id and message_id in self._seen_message_ids:
            return None
        if message_id:
            self._seen_message_ids.add(message_id)

        normalized = extract_usage(data)
        if normalized.is_fully_zero():
            # A trailing zero USAGE must not wipe the last real row.
            return None
        self._last_nonzero = normalized
        return normalized

    def last_nonzero(self) -> NormalizedUsage | None:
        """Last non-zero usage seen this turn (for diagnostics / fallback)."""
        return self._last_nonzero


def normalize_usage_event(event: AgentEvent) -> AgentEvent | None:
    """Stateless field-projection for a USAGE ``AgentEvent`` (CLAW-P0b).

    Returns a USAGE ``AgentEvent`` with canonical fields (cache_* tokens,
    ``token_source``) and vendor aliases collapsed, or ``None`` when the
    event is fully-zero and should be dropped. Does **not** dedupe across
    events — use ``TokenNormalizer`` for per-turn dedupe. Non-USAGE events
    pass through unchanged.
    """
    if event.type != AgentEventType.USAGE:
        return event
    if not isinstance(event.data, dict):
        return event
    normalized = extract_usage(event.data)
    if normalized.is_fully_zero():
        return None
    # Preserve passthrough keys the bridge may have sent, but drop the
    # collapsed aliases so the transcript never carries duplicate shapes.
    passthrough = {
        k: v
        for k, v in event.data.items()
        if k not in set(_USAGE_FIELD_ALIASES.values()) | set(_USAGE_FIELD_ALIASES.keys())
    }
    return AgentEvent(type=event.type, data={**passthrough, **normalized.to_payload()}, at=event.at)

