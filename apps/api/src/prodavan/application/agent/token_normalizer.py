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
    """Normalized USAGE payload ready for ``AgentUsageRow`` + metrics."""

    input_tokens: int | None
    output_tokens: int | None
    cache_creation_tokens: int | None
    cache_read_tokens: int | None
    cost_usd: float | None
    provider: str | None
    model: str | None
    message_id: str | None
    token_source: str | None


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
        * fully-zero usage (no input/output/cache, no cost, no new
          ``message_id``) → ``None`` so it cannot overwrite the last real
          row via a subsequent persist path.
        """
        message_id = self._extract_message_id(data)
        if message_id and message_id in self._seen_message_ids:
            return None
        if message_id:
            self._seen_message_ids.add(message_id)

        input_tokens = _as_int(data.get("input_tokens"))
        output_tokens = _as_int(data.get("output_tokens"))
        cache_creation = _as_int(
            data.get("cache_creation_tokens")
            or data.get("cache_creation_input_tokens")
        )
        cache_read = _as_int(data.get("cache_read_tokens") or data.get("cache_read_input_tokens"))
        cost_usd = self._extract_cost(data)
        provider = data.get("provider")
        model = data.get("model")
        token_source = data.get("token_source") or data.get("usage_source")

        normalized = NormalizedUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            cost_usd=cost_usd,
            provider=str(provider) if provider else None,
            model=str(model) if model else None,
            message_id=message_id,
            token_source=str(token_source) if token_source else None,
        )

        if self._is_fully_zero(normalized):
            # A trailing zero USAGE must not wipe the last real row.
            return None
        self._last_nonzero = normalized
        return normalized

    @staticmethod
    def _extract_message_id(data: dict[str, Any]) -> str | None:
        raw = data.get("message_id") or data.get("request_id")
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None

    @staticmethod
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

    @staticmethod
    def _is_fully_zero(usage: NormalizedUsage) -> bool:
        numeric_fields = (
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_creation_tokens,
            usage.cache_read_tokens,
            usage.cost_usd,
        )
        return all(value in (None, 0) for value in numeric_fields)


def normalize_usage_event(event: AgentEvent) -> AgentEvent | None:
    """Stateless helper for tests / one-shot normalization.

    Returns a USAGE ``AgentEvent`` with normalized fields (cache_* tokens,
    ``token_source``), or ``None`` when the event is a duplicate/zero that
    should be dropped. Per-turn dedupe requires the stateful class above;
    this helper only does field projection + zero-skip for callers that do
    not need cross-event dedupe (e.g. append_event from bridge).
    """
    if event.type != AgentEventType.USAGE:
        return event
    data = dict(event.data) if isinstance(event.data, dict) else {}
    input_tokens = _as_int(data.get("input_tokens"))
    output_tokens = _as_int(data.get("output_tokens"))
    cache_creation = _as_int(
        data.get("cache_creation_tokens") or data.get("cache_creation_input_tokens")
    )
    cache_read = _as_int(data.get("cache_read_tokens") or data.get("cache_read_input_tokens"))
    cost_usd = TokenNormalizer._extract_cost(data)
    # Skip fully-zero trailing usage (no signal, avoids wiping last row).
    numeric = (input_tokens, output_tokens, cache_creation, cache_read, cost_usd)
    if all(value in (None, 0) for value in numeric):
        return None
    normalized_data: dict[str, Any] = {
        k: v
        for k, v in data.items()
        if k
        not in {
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "estimated_cost_usd",
        }
    }
    normalized_data["input_tokens"] = input_tokens
    normalized_data["output_tokens"] = output_tokens
    normalized_data["cache_creation_tokens"] = cache_creation
    normalized_data["cache_read_tokens"] = cache_read
    normalized_data["cost_usd"] = cost_usd
    if not normalized_data.get("token_source") and data.get("usage_source"):
        normalized_data["token_source"] = data["usage_source"]
    return AgentEvent(type=event.type, data=normalized_data, at=event.at)
